"""Tests for syncing real emotion from dorm training popup, and emotion shared by game fleets (no device interaction)."""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import module.campaign.sortie_status as sortie_status
from module.campaign.run import CampaignRun
from module.combat.emotion import Emotion
from module.combat.emotion_sync import EmotionSync, dorm_floor_reversed, emotion_apply, fleet_lowest, parse_ships
from module.config.config import AzurLaneConfig
from module.config.utils import filepath_config, read_file
from module.exception import ScriptEnd

AUTO = 'fleet1_mob_fleet2_boss_auto_swap'
# Real case: dorm training (boss fleet) lowest 33 at 40/h, rest (mob fleet) lowest 44 at 50/h
RESULT = {'training': (33, 40), 'rest': (44, 50)}


class TestParse(unittest.TestCase):
    def test_parse_ships(self):
        # Real OCR results, `h` is not in the alphabet, empty card has no recover speed
        ships = parse_ships([140, 140, 147, 147, 39, 0], ['50/', '50/', '50/1', '50/', '50/', ''])
        self.assertEqual(ships, [(140, 50), (140, 50), (147, 50), (147, 50), (39, 50)])
        self.assertEqual(fleet_lowest(ships), (39, 50))
        self.assertIsNone(fleet_lowest([]))
        # Digits dropped without `/` are not accepted
        self.assertEqual(parse_ships([58], ['40']), [])

    def test_dorm_floor_reversed(self):
        # Real case: boss fleet in training is lower than mob fleet in rest
        self.assertTrue(dorm_floor_reversed({'training': (41, 40), 'rest': (150, 50)}))
        self.assertFalse(dorm_floor_reversed({'training': (150, 40), 'rest': (41, 50)}))
        self.assertFalse(dorm_floor_reversed({'training': (80, 40), 'rest': (80, 50)}))
        self.assertFalse(dorm_floor_reversed({'training': (41, 40), 'rest': None}))
        self.assertFalse(dorm_floor_reversed({}))


class ConfigTestBase(unittest.TestCase):
    """Uses a temporary config file and status folder."""
    name = 'zz_test_emotion_sync'

    def setUp(self):
        template = read_file(filepath_config('template'))
        for task in ['Main', 'Event']:
            template[task]['Fleet']['Fleet1'] = 1
            template[task]['Fleet']['Fleet2'] = 2
            template[task]['Fleet']['FleetOrder'] = AUTO
        # Main3 uses other game fleets
        template['Main3']['Fleet']['Fleet1'] = 3
        template['Main3']['Fleet']['Fleet2'] = 4
        self.path = filepath_config(self.name)
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(template, f, default=str)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))

        folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, folder, True)
        patcher = patch.object(sortie_status, 'STATUS_FOLDER', os.path.join(folder, 'status'))
        patcher.start()
        self.addCleanup(patcher.stop)

    def config(self, task='Main'):
        return AzurLaneConfig(self.name, task=task)

    def emotion_of(self, task):
        data = read_file(self.path)[task]['Emotion']
        return {key: data[key] for key in ['Fleet1Value', 'Fleet1Recover', 'Fleet1Oath',
                                           'Fleet2Value', 'Fleet2Recover', 'Fleet2Oath']}


class TestEmotionApply(ConfigTestBase):
    def test_apply_to_shared_tasks(self):
        emotion = Emotion(self.config('Main'))
        self.assertTrue(emotion_apply(emotion, RESULT))
        expected = {'Fleet1Value': 44, 'Fleet1Recover': 'dormitory_floor_2', 'Fleet1Oath': False,
                    'Fleet2Value': 33, 'Fleet2Recover': 'dormitory_floor_1', 'Fleet2Oath': False}
        self.assertEqual(self.emotion_of('Main'), expected)
        # Event uses the same game fleets
        self.assertEqual(self.emotion_of('Event'), expected)
        # Main3 uses other game fleets
        self.assertEqual(self.emotion_of('Main3')['Fleet1Value'], 119)
        status = sortie_status.status_read(self.name)[sortie_status.EMOTION_SYNC_KEY]
        self.assertEqual(status['fleets']['1'], {'value': 44, 'speed': 50, 'tab': 'rest'})
        self.assertEqual(status['fleets']['2'], {'value': 33, 'speed': 40, 'tab': 'training'})
        # Boss fleet lower than mob fleet
        self.assertTrue(status['reversed'])

    def test_not_reversed(self):
        self.assertTrue(emotion_apply(Emotion(self.config('Main')), {'training': (120, 40), 'rest': (60, 50)}))
        status = sortie_status.status_read(self.name)[sortie_status.EMOTION_SYNC_KEY]
        self.assertFalse(status['reversed'])

    def test_order_of_user_config(self):
        # Fleet order swapped in this sortie, dorm tabs follow the fleet order in user config
        config = self.config('Main')
        config.override(Fleet_FleetOrder='fleet1_boss_fleet2_mob')
        self.assertTrue(emotion_apply(Emotion(config), RESULT))
        self.assertEqual(self.emotion_of('Main')['Fleet1Value'], 44)

    def test_reversed_order(self):
        config = self.config('Main')
        config.cross_set('Main.Fleet.FleetOrder', 'fleet1_boss_fleet2_mob')
        self.assertTrue(emotion_apply(Emotion(self.config('Main')), RESULT))
        self.assertEqual(self.emotion_of('Main')['Fleet1Value'], 33)
        self.assertEqual(self.emotion_of('Main')['Fleet2Value'], 44)

    def test_standby_order_not_synced(self):
        self.config('Main').cross_set('Main.Fleet.FleetOrder', 'fleet1_all_fleet2_standby')
        self.assertFalse(emotion_apply(Emotion(self.config('Main')), RESULT))
        self.assertEqual(self.emotion_of('Main')['Fleet1Value'], 119)

    def test_unknown_speed(self):
        self.assertTrue(emotion_apply(Emotion(self.config('Main')), {'training': (33, 45), 'rest': None}))
        emotion = self.emotion_of('Main')
        self.assertEqual(emotion['Fleet2Value'], 33)
        # Recover settings unchanged
        self.assertEqual(emotion['Fleet2Recover'], 'not_in_dormitory')
        # Mob fleet not read, calculated emotion kept
        self.assertEqual(emotion['Fleet1Value'], 119)

    def test_nothing_read(self):
        self.assertFalse(emotion_apply(Emotion(self.config('Main')), {}))
        self.assertNotIn(sortie_status.EMOTION_SYNC_KEY, sortie_status.status_read(self.name))


class TestSharedEmotion(ConfigTestBase):
    def test_latest_record_wins(self):
        # Event fought later, Main reads emotion of the same game fleet from it
        config = self.config('Event')
        emotion = Emotion(config)
        emotion.update()
        emotion.fleet_1.current = 60
        emotion.record()
        main = Emotion(self.config('Main'))
        self.assertEqual(main.fleet_1.value, 60)
        main.update()
        self.assertEqual(main.fleet_1.current, 60)
        # Main3 uses another game fleet
        self.assertEqual(Emotion(self.config('Main3')).fleet_1.value, 119)

    def test_gems_farming_not_shared(self):
        config = self.config('Event')
        emotion = Emotion(config)
        emotion.update()
        emotion.fleet_1.current = 60
        emotion.record()
        # GemsFarming doesn't read emotion of other tasks, and isn't written by them
        self.assertEqual(Emotion(self.config('GemsFarming')).fleet_1.value,
                         read_file(self.path)['GemsFarming']['Emotion']['Fleet1Value'])
        self.assertNotEqual(read_file(self.path)['GemsFarming']['Emotion']['Fleet1Value'], 60)

    def test_own_override(self):
        # Overridden value of current task is used when it's the latest
        config = self.config('Main')
        config.override(Emotion_Fleet1Value=80, Emotion_Fleet1Record=datetime.now() + timedelta(minutes=1))
        self.assertEqual(Emotion(config).fleet_1.value, 80)


class FakeEmotion:
    is_calculate = True


class FakeDevice:
    image = None

    def screenshot(self):
        self.image = 'image'
        return self.image


class FakeCampaign:
    emotion = FakeEmotion()

    def __init__(self, in_map=False):
        self.device = FakeDevice()
        self.in_map = in_map

    def is_in_map(self):
        return self.in_map

    def is_in_auto_search_menu(self):
        return False


class FakeTask:
    def __init__(self, command):
        self.command = command


class FakeRunConfig:
    def __init__(self, command='Main'):
        self.task = FakeTask(command)
        self.delays = []

    def task_delay(self, **kwargs):
        self.delays.append(kwargs)


@patch('module.config.server.server', 'jp')
class TestEmotionSyncRetry(unittest.TestCase):
    def run_of(self, command='Main', in_map=False):
        run = CampaignRun.__new__(CampaignRun)
        run.config = FakeRunConfig(command)
        run.device = FakeDevice()
        run.campaign = FakeCampaign(in_map=in_map)
        run.emotion_synced = False
        return run

    @patch.object(EmotionSync, '__init__', lambda self, config, device: None)
    def test_retry_once(self):
        run = self.run_of()
        with patch.object(EmotionSync, 'emotion_sync', return_value=True) as sync:
            self.assertTrue(run.emotion_sync_retry(ScriptEnd('Emotion control')))
            # Delay of calculated emotion is undone
            self.assertEqual(run.config.delays, [{'minute': 0}])
            # Still not enough with real emotion, delayed by check_reduce() again
            self.assertFalse(run.emotion_sync_retry(ScriptEnd('Emotion control')))
            self.assertEqual(sync.call_count, 1)

    @patch.object(EmotionSync, '__init__', lambda self, config, device: None)
    def test_sync_failed(self):
        run = self.run_of()
        with patch.object(EmotionSync, 'emotion_sync', return_value=False):
            self.assertFalse(run.emotion_sync_retry(ScriptEnd('Emotion control')))
        self.assertEqual(run.config.delays, [])

    @patch.object(EmotionSync, '__init__', lambda self, config, device: None)
    def test_sync_at_start(self):
        run = self.run_of()
        with patch.object(EmotionSync, 'emotion_sync', return_value=True) as sync:
            self.assertTrue(run.emotion_sync_start())
            # Already synced in this task, delayed with real emotion directly
            self.assertFalse(run.emotion_sync_retry(ScriptEnd('Emotion control')))
            self.assertEqual(sync.call_count, 1)
        self.assertEqual(run.config.delays, [])

    @patch.object(EmotionSync, '__init__', lambda self, config, device: None)
    def test_start_in_map(self):
        # Task restarted in map, can't go to dorm before retreating
        run = self.run_of(in_map=True)
        with patch.object(EmotionSync, 'emotion_sync', return_value=True) as sync:
            self.assertFalse(run.emotion_sync_start())
            sync.assert_not_called()
            # Synced when emotion is not enough
            self.assertTrue(run.emotion_sync_retry(ScriptEnd('Emotion control')))
            self.assertEqual(sync.call_count, 1)

    def test_not_synced(self):
        with patch.object(EmotionSync, 'emotion_sync') as sync:
            self.assertFalse(self.run_of().emotion_sync_retry(ScriptEnd('Reach condition')))
            self.assertFalse(self.run_of('GemsFarming').emotion_sync_retry(ScriptEnd('Emotion control')))
            with patch('module.config.server.server', 'cn'):
                self.assertFalse(self.run_of().emotion_sync_retry(ScriptEnd('Emotion control')))
                self.assertFalse(self.run_of().emotion_sync_start())
            self.assertFalse(self.run_of('GemsFarming').emotion_sync_start())
            # Other campaign tasks may use other fleets
            self.assertFalse(self.run_of('Main3').emotion_sync_start())
            self.assertFalse(self.run_of('EventA').emotion_sync_start())
            self.assertTrue(self.run_of('Event').emotion_sync_available())
            sync.assert_not_called()


if __name__ == '__main__':
    unittest.main()
