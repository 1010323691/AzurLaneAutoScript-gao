"""Tests for Fleet.FleetOrder=fleet1_mob_fleet2_boss_fleet3_rotate (no device interaction)."""
import json
import os
import shutil
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

import module.campaign.sortie_status as sortie_status
from module.campaign.sortie_status import build_groups, build_rows, emotion_slots, latest_emotion
from module.combat.emotion import Emotion
from module.combat.emotion_sync import emotion_apply
from module.config.config import AzurLaneConfig
from module.config.utils import filepath_config, read_file
from module.exception import ScriptEnd

ROTATE = 'fleet1_mob_fleet2_boss_fleet3_rotate'
NOW = datetime(2026, 10, 31, 18, 0, 0)


class FakeConfig:
    def __init__(self, v1, v2, v3, rotate=True, order='fleet1_mob_fleet2_boss',
                 control3='prevent_green_face', recover3='not_in_dormitory'):
        now = datetime.now()
        self.FLEET_ORDER_AUTO_SWAP = False
        self.FLEET_ORDER_ROTATE = rotate
        self.FLEET_ROTATE_BASE = 1
        self.Fleet_FleetOrder = order
        self.Fleet_Fleet1 = 1
        self.Fleet_Fleet2 = 2
        self.Fleet_Fleet3 = 3
        self.Emotion_Mode = 'calculate'
        self.Campaign_Use2xBook = False
        for i, v in [(1, v1), (2, v2), (3, v3)]:
            setattr(self, f'Emotion_Fleet{i}Value', v)
            setattr(self, f'Emotion_Fleet{i}Record', now)
            setattr(self, f'Emotion_Fleet{i}Control', control3 if i == 3 else 'prevent_green_face')
            setattr(self, f'Emotion_Fleet{i}Recover', recover3 if i == 3 else 'not_in_dormitory')
            setattr(self, f'Emotion_Fleet{i}Oath', False)
        self.delays = []

    def override(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def set_record(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

    def task_delay(self, **kwargs):
        self.delays.append(kwargs)


def check(config, battle=4):
    """
    4 battles: mob fleet 3 battles (6 emotion), boss fleet 1 battle (2 emotion).

    Returns:
        str: 'enter' or 'delay'
    """
    try:
        Emotion(config).check_reduce(battle)
        return 'enter'
    except ScriptEnd:
        return 'delay'


class TestEmotionRotate(unittest.TestCase):
    def test_higher_mob_fleet_chosen(self):
        c = FakeConfig(60, 119, 100)
        self.assertEqual(check(c), 'enter')
        self.assertEqual(c.Fleet_Fleet1, 3)
        # Rotate back next sortie when fleet 1 is higher
        c.Emotion_Fleet3Value = 50
        self.assertEqual(check(c), 'enter')
        self.assertEqual(c.Fleet_Fleet1, 1)

    def test_enough_one_chosen_over_higher(self):
        # Fleet 3 is higher but keeps happy bonus (>120), not enough
        c = FakeConfig(60, 119, 110, control3='keep_exp_bonus', recover3='dormitory_floor_1')
        self.assertEqual(check(c), 'enter')
        self.assertEqual(c.Fleet_Fleet1, 1)

    def test_neither_enough_delays_to_earlier(self):
        c = FakeConfig(30, 119, 40)
        self.assertEqual(check(c), 'delay')
        self.assertEqual(len(c.delays), 1)
        # Fleet 3 recovers first, and is used after delay
        self.assertEqual(c.Fleet_Fleet1, 3)
        expected = Emotion(c).fleet_3
        expected.update()
        self.assertEqual(c.delays[0]['target'], expected.get_recovered(6))

    def test_boss_fleet_low_delays(self):
        c = FakeConfig(100, 30, 100)
        self.assertEqual(check(c), 'delay')

    def test_reduce_rotated_fleet(self):
        c = FakeConfig(60, 119, 100)
        emotion = Emotion(c)
        emotion.check_reduce(4)
        self.assertIs(emotion.fleets[0], emotion.fleet_3)
        emotion.reduce(1)
        self.assertEqual(c.Emotion_Fleet3Value, 98)
        self.assertEqual(c.Emotion_Fleet1Value, 60)
        # A new Emotion object follows the overridden Fleet_Fleet1
        self.assertIs(Emotion(c).fleets[0].key, 3)

    def test_rotate_disabled_ignores_fleet3(self):
        c = FakeConfig(30, 119, 100, rotate=False)
        self.assertEqual(check(c), 'delay')
        self.assertEqual(c.Fleet_Fleet1, 1)
        self.assertEqual([f.key for f in Emotion(c).all_fleets], [1, 2])

    def test_check_swap_without_delay(self):
        c = FakeConfig(30, 119, 40)
        Emotion(c).check_swap(4)
        self.assertEqual(c.delays, [])
        self.assertEqual(c.Fleet_Fleet1, 3)

    def test_clear_all(self):
        c = FakeConfig(60, 119, 100)
        Emotion(c).check_clear_all(10)
        self.assertEqual(c.Fleet_Fleet1, 3)
        # 10 battles needs 40 + 18 emotion, capped at 119 is reachable, delay
        c = FakeConfig(30, 119, 40)
        with self.assertRaises(ScriptEnd):
            Emotion(c).check_clear_all(10)


class ConfigTestBase(unittest.TestCase):
    """Uses a temporary config file and status folder."""
    name = 'zz_test_fleet_rotate'
    fleet3 = 3

    def setUp(self):
        template = read_file(filepath_config('template'))
        for task in ['Main', 'Event']:
            template[task]['Fleet']['Fleet1'] = 1
            template[task]['Fleet']['Fleet2'] = 2
            template[task]['Fleet']['Fleet3'] = self.fleet3
        template['Main']['Fleet']['FleetOrder'] = ROTATE
        # Main2 uses game fleet 3 without rotating
        template['Main2']['Fleet']['Fleet1'] = 3
        template['Main2']['Fleet']['Fleet2'] = 4
        template['Main2']['Fleet']['Fleet3'] = 1
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


class TestConfigBindRotate(ConfigTestBase):
    def test_bind_resolves_pseudo_order(self):
        config = self.config('Main')
        self.assertTrue(config.FLEET_ORDER_ROTATE)
        self.assertFalse(config.FLEET_ORDER_AUTO_SWAP)
        self.assertEqual(config.Fleet_FleetOrder, 'fleet1_mob_fleet2_boss')
        self.assertEqual(config.FLEET_ROTATE_BASE, 1)
        self.assertEqual(config.FLEET_BOSS, 2)

    def test_bind_keeps_rotated_override(self):
        config = self.config('Main')
        config.override(Fleet_Fleet1=3)
        config.bind('Main')
        self.assertTrue(config.FLEET_ORDER_ROTATE)
        self.assertEqual(config.Fleet_Fleet1, 3)
        self.assertEqual(config.FLEET_ROTATE_BASE, 1)
        self.assertEqual(Emotion(config).fleets[0].key, 3)

    def test_other_task_not_rotate(self):
        self.assertFalse(self.config('Event').FLEET_ORDER_ROTATE)
        self.assertFalse(self.config('Main2').FLEET_ORDER_ROTATE)

    def test_emotion_slots(self):
        data = read_file(self.path)
        # Fleet 3 of Main is shared with Main2 slot 1, Fleet 3 of Event and Main2 are not used
        self.assertEqual(emotion_slots(data, 3), [('Main', 3), ('Main2', 1)])
        self.assertNotIn(('Main2', 3), emotion_slots(data, 1))

    def test_record_shared(self):
        config = self.config('Main')
        emotion = Emotion(config)
        emotion.update()
        emotion.fleet_3.current = 77
        emotion.record()
        data = read_file(self.path)
        self.assertEqual(data['Main']['Emotion']['Fleet3Value'], 77)
        self.assertEqual(data['Main2']['Emotion']['Fleet1Value'], 77)
        self.assertEqual(latest_emotion(data, 3)[0], 77)

    def test_emotion_apply(self):
        emotion = Emotion(self.config('Main'))
        self.assertTrue(emotion_apply(emotion, {'training': (33, 40), 'rest': (44, 50)}))
        data = read_file(self.path)
        main = data['Main']['Emotion']
        self.assertEqual((main['Fleet3Value'], main['Fleet3Recover']), (33, 'dormitory_floor_1'))
        self.assertEqual((main['Fleet1Value'], main['Fleet1Recover']), (44, 'dormitory_floor_2'))
        self.assertEqual(main['Fleet2Recover'], 'not_in_dormitory')
        # Game fleet 3 in other tasks
        self.assertEqual(data['Main2']['Emotion']['Fleet1Value'], 33)
        sync = sortie_status.status_read(self.name)['_EmotionSync']
        self.assertEqual(sync['fleets']['3']['tab'], 'training')
        self.assertEqual(sync['fleets']['1']['tab'], 'rest')
        self.assertNotIn('2', sync['fleets'])
        self.assertFalse(sync['reversed'])


class TestConfigBindRotateInvalid(ConfigTestBase):
    fleet3 = 0

    def test_fleet3_unset_disables_rotate(self):
        config = self.config('Main')
        self.assertFalse(config.FLEET_ORDER_ROTATE)
        self.assertEqual(config.Fleet_FleetOrder, 'fleet1_mob_fleet2_boss')
        self.assertEqual([f.key for f in Emotion(config).all_fleets], [1, 2])


def task_data(order=ROTATE, fleet3=3, value3=90):
    return {
        'Scheduler': {'Enable': True, 'NextRun': '2026-10-31 18:48:00'},
        'Campaign': {'Name': '7-3'},
        'Fleet': {'Fleet1': 1, 'Fleet2': 2, 'Fleet3': fleet3, 'FleetOrder': order},
        'Emotion': {
            'Mode': 'calculate',
            'Fleet1Value': 52, 'Fleet1Record': '2026-10-31 18:00:00', 'Fleet1Control': 'prevent_green_face',
            'Fleet1Recover': 'dormitory_floor_2', 'Fleet1Oath': False,
            'Fleet2Value': 50, 'Fleet2Record': '2026-10-31 18:00:00', 'Fleet2Control': 'prevent_green_face',
            'Fleet2Recover': 'not_in_dormitory', 'Fleet2Oath': False,
            'Fleet3Value': value3, 'Fleet3Record': '2026-10-31 18:00:00', 'Fleet3Control': 'prevent_green_face',
            'Fleet3Recover': 'dormitory_floor_1', 'Fleet3Oath': False,
        },
    }


class TestSortieStatusRotate(unittest.TestCase):
    def test_three_fleets_not_running(self):
        row = build_rows({'Main': task_data()}, {}, now=NOW)[0]
        self.assertTrue(row['rotate'])
        self.assertEqual([(f['number'], f['role']) for f in row['fleets']], [(1, 'mob'), (2, 'boss'), (3, 'mob')])
        self.assertEqual(row['fleets'][2]['value'], 90)

    def test_running_mob_fleet3(self):
        status = {'Main': {'state': 'battle', 'fleet': 1, 'battle': 2, 'mob': 3}}
        row = build_rows({'Main': task_data()}, status, running_task='Main', now=NOW)[0]
        roles = [(f['number'], f['role'], f['fighting']) for f in row['fleets']]
        self.assertEqual(roles, [(1, 'rest', False), (2, 'boss', False), (3, 'mob', True)])
        status['Main']['fleet'] = 2
        row = build_rows({'Main': task_data()}, status, running_task='Main', now=NOW)[0]
        self.assertEqual([f['fighting'] for f in row['fleets']], [False, True, False])

    def test_fleet3_ignored_without_rotate(self):
        row = build_rows({'Main': task_data(order='fleet1_mob_fleet2_boss')}, {}, now=NOW)[0]
        self.assertFalse(row['rotate'])
        self.assertEqual([f['number'] for f in row['fleets']], [1, 2])
        # Invalid fleet 3, same as AzurLaneConfig.bind()
        row = build_rows({'Main': task_data(fleet3=2)}, {}, now=NOW)[0]
        self.assertFalse(row['rotate'])
        self.assertEqual([f['number'] for f in row['fleets']], [1, 2])

    def test_dorm_hint(self):
        groups = build_groups({'Main': task_data()}, {}, now=NOW)
        self.assertEqual(groups[0]['dorm'], 3)
        groups = build_groups({'Main': task_data(order='fleet1_mob_fleet2_boss')}, {}, now=NOW)
        self.assertEqual(groups[0]['dorm'], 2)
        groups = build_groups({'Main': task_data(order='fleet1_all_fleet2_standby')}, {}, now=NOW)
        self.assertIsNone(groups[0]['dorm'])
        # Reversed warning is for mob fleet and boss fleet only
        status = {'_EmotionSync': {'time': '2026-10-31 17:50:00', 'reversed': True,
                                   'fleets': {'1': {'tab': 'rest'}, '3': {'tab': 'training'}}}}
        self.assertFalse(build_groups({'Main': task_data()}, status, now=NOW)[0]['reversed'])


if __name__ == '__main__':
    unittest.main()
