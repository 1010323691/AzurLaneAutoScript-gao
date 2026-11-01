"""Tests for sortie status shown in GUI overview (no device interaction)."""
import os
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import module.campaign.sortie_status as sortie_status
from module.campaign.sortie_status import build_groups, build_rows, emotion_current, emotion_slots, format_time, \
    emotion_synced_recently, latest_emotion, stage_name, status_battle, status_read, status_update
from module.combat.emotion import FleetEmotion

NOW = datetime(2026, 10, 31, 18, 0, 0)


class FakeTask:
    def __init__(self, command):
        self.command = command


class FakeConfig:
    def __init__(self, task='Main', name='zz_test_sortie'):
        self.task = FakeTask(task)
        self.config_name = name


class FakeEmotionConfig:
    Emotion_Fleet1Value = 100
    Emotion_Fleet1Record = datetime(2026, 10, 31, 17, 0, 0)
    Emotion_Fleet1Recover = 'dormitory_floor_1'
    Emotion_Fleet1Control = 'prevent_green_face'
    Emotion_Fleet1Oath = False


def task_data(enable=True, fleet2=2, order='fleet1_mob_fleet2_boss_auto_swap', name='7-3',
              value1=52, value2=50, next_run='2026-10-31 18:48:00'):
    return {
        'Scheduler': {'Enable': enable, 'NextRun': next_run},
        'Campaign': {'Name': name},
        'Fleet': {'Fleet1': 1, 'Fleet2': fleet2, 'FleetOrder': order},
        'Emotion': {
            'Mode': 'calculate',
            'Fleet1Value': value1, 'Fleet1Record': '2026-10-31 18:00:00', 'Fleet1Control': 'prevent_green_face',
            'Fleet1Recover': 'dormitory_floor_1', 'Fleet1Oath': False,
            'Fleet2Value': value2, 'Fleet2Record': '2026-10-31 18:00:00', 'Fleet2Control': 'prevent_green_face',
            'Fleet2Recover': 'not_in_dormitory', 'Fleet2Oath': False,
        },
    }


class TestEmotionCurrent(unittest.TestCase):
    def test_same_as_fleet_emotion(self):
        fleet = FleetEmotion(FakeEmotionConfig(), fleet=1)
        fleet.update()
        expected = emotion_current(100, FakeEmotionConfig.Emotion_Fleet1Record, 4, 150)
        self.assertEqual(fleet.current, expected)

    def test_recover_and_max(self):
        record = datetime(2026, 10, 31, 17, 0, 0)
        # 10 recovers of 6 min in an hour
        self.assertEqual(emotion_current(50, record, 4, 150, now=NOW), 90)
        self.assertEqual(emotion_current(140, record, 4, 150, now=NOW), 150)
        # Record in the future doesn't reduce emotion
        self.assertEqual(emotion_current(50, NOW + timedelta(hours=1), 4, 150, now=NOW), 50)
        self.assertEqual(emotion_current(-5, record, 2, 119, now=NOW), 20)


class TestStatusFile(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.folder, True)
        patcher = patch.object(sortie_status, 'STATUS_FOLDER', os.path.join(self.folder, 'status'))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_update_merge_and_tasks(self):
        config = FakeConfig('Main')
        status_update(config, reset=True, state='map', stage='7-3', battle=0, battle_total=5)
        status_battle(config, 1)
        status_battle(config, 2)
        status_update(FakeConfig('Event'), state='emotion_delay', until=NOW)
        data = status_read('zz_test_sortie')
        self.assertEqual(data['Main']['state'], 'battle')
        self.assertEqual(data['Main']['battle'], 2)
        self.assertEqual(data['Main']['fleet'], 2)
        self.assertEqual(data['Main']['stage'], '7-3')
        self.assertEqual(data['Event']['until'], '2026-10-31 18:00:00')
        # New map drops previous status
        status_update(config, reset=True, state='map', battle=0)
        self.assertNotIn('stage', status_read('zz_test_sortie')['Main'])

    def test_oil_limit_delay(self):
        # Real case: auto search stopped at oil 928 < OilLimit 1000, Main delayed 2~4 hours
        from module.campaign.run import CampaignRun

        class FakeCampaign:
            auto_search_oil_limit_triggered = True
            auto_search_oil = 928

        class FakeRunConfig(FakeConfig):
            StopCondition_OilLimit = 1000
            StopCondition_ReachLevel = 0

            def task_delay(self, **kwargs):
                self.delay = kwargs

        run = CampaignRun.__new__(CampaignRun)
        run.config = FakeRunConfig('Main')
        run.campaign = FakeCampaign()
        run.run_limit = 0
        before = datetime.now().replace(microsecond=0)
        self.assertTrue(run.triggered_stop_condition(oil_check=False))
        until = run.config.delay['target']
        self.assertTrue(before + timedelta(minutes=120) <= until <= datetime.now() + timedelta(minutes=240))
        data = status_read('zz_test_sortie')['Main']
        self.assertEqual(data['state'], 'oil_limit')
        self.assertEqual(data['until'], until.strftime('%Y-%m-%d %H:%M:%S'))
        self.assertEqual((data['oil'], data['oil_limit']), (928, 1000))
        # Oil limit is at least 500
        run.config.StopCondition_OilLimit = 0
        run.oil_limit_delay(300)
        self.assertEqual(status_read('zz_test_sortie')['Main']['oil_limit'], 500)
        # Sortie again, entering map drops it
        status_update(run.config, reset=True, state='map', battle=0)
        self.assertEqual(status_read('zz_test_sortie')['Main'].get('state'), 'map')

    def test_no_task(self):
        config = FakeConfig()
        del config.task
        status_update(config, state='map')
        self.assertEqual(status_read('zz_test_sortie'), {})

    def test_write_failed_no_raise(self):
        with patch.object(sortie_status.os, 'replace', side_effect=OSError('denied')):
            status_update(FakeConfig(), state='map')
        self.assertEqual(status_read('zz_test_sortie'), {})

    def test_broken_file(self):
        os.makedirs(sortie_status.STATUS_FOLDER)
        with open(sortie_status.status_file('zz_test_sortie'), 'w') as f:
            f.write('{broken')
        self.assertEqual(status_read('zz_test_sortie'), {})
        status_battle(FakeConfig(), 1)
        self.assertEqual(status_read('zz_test_sortie')['Main']['battle'], 1)


class TestBuildRows(unittest.TestCase):
    def test_battle(self):
        data = {'Main': task_data(), 'Commission': {'Scheduler': {'Enable': True}}}
        status = {'Main': {'state': 'battle', 'stage': '7-3', 'order': 'fleet1_boss_fleet2_mob',
                           'battle': 3, 'battle_total': 5, 'fleet': 2}}
        rows = build_rows(data, status, running_task='Main', now=NOW)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row['state'], 'battle')
        self.assertEqual((row['battle'], row['battle_total']), (3, 5))
        self.assertTrue(row['running'])
        self.assertTrue(row['auto_swap'])
        # Fleet order swapped in this sortie
        self.assertEqual([f['role'] for f in row['fleets']], ['boss', 'mob'])
        self.assertEqual([f['fighting'] for f in row['fleets']], [False, True])
        self.assertEqual([f['value'] for f in row['fleets']], [52, 50])
        self.assertEqual([f['max'] for f in row['fleets']], [150, 119])
        self.assertEqual(row['fleets'][0]['limit'], 40)

    def test_emotion_wait_in_map(self):
        status = {'Main': {'state': 'emotion_wait', 'fleet': 1, 'until': '2026-10-31 18:30:00'}}
        row = build_rows({'Main': task_data()}, status, running_task='Main', now=NOW)[0]
        self.assertEqual(row['state'], 'emotion_wait')
        self.assertEqual(row['until'], datetime(2026, 10, 31, 18, 30))
        self.assertTrue(row['fleets'][0]['fighting'])

    def test_emotion_waited(self):
        # Emotion waited in map, going to battle, progress is kept
        status = {'Main': {'state': 'emotion_wait', 'fleet': 2, 'until': '2026-10-31 17:50:00',
                           'battle': 3, 'battle_total': 5}}
        row = build_rows({'Main': task_data()}, status, running_task='Main', now=NOW)[0]
        self.assertEqual(row['state'], 'battle')
        self.assertEqual((row['battle'], row['battle_total']), (3, 5))
        self.assertTrue(row['fleets'][1]['fighting'])

    def test_emotion_delay(self):
        # Real case: Main delayed for emotion, shown in waiting tasks
        status = {'Main': {'state': 'emotion_delay', 'until': '2026-10-31 18:48:00', 'order': 'fleet1_boss_fleet2_mob'}}
        row = build_rows({'Main': task_data()}, status, running_task='Commission', now=NOW)[0]
        self.assertEqual(row['state'], 'emotion_delay')
        self.assertEqual(format_time(row['until'], now=NOW), '18:48')
        self.assertFalse(row['running'])
        # Order of previous sortie is not shown when not running
        self.assertEqual([f['role'] for f in row['fleets']], ['mob', 'boss'])

    def test_oil_limit(self):
        status = {'Main': {'state': 'oil_limit', 'until': '2026-10-31 18:48:00', 'oil': 928, 'oil_limit': 1000}}
        row = build_rows({'Main': task_data()}, status, now=NOW)[0]
        self.assertEqual(row['state'], 'oil_limit')
        self.assertEqual(format_time(row['until'], now=NOW), '18:48')
        self.assertEqual((row['oil'], row['oil_limit']), (928, 1000))
        # Running again, not shown even before entering map
        row = build_rows({'Main': task_data()}, status, running_task='Main', now=NOW)[0]
        self.assertEqual(row['state'], 'running')
        # NextRun changed, e.g. by user
        row = build_rows({'Main': task_data(next_run='2026-10-31 18:20:00')}, status, now=NOW)[0]
        self.assertEqual(row['state'], 'waiting')

    def test_next_run_changed(self):
        # NextRun changed after emotion delay, e.g. by user
        status = {'Main': {'state': 'emotion_delay', 'until': '2026-10-31 19:30:00'}}
        row = build_rows({'Main': task_data()}, status, now=NOW)[0]
        self.assertEqual(row['state'], 'waiting')
        self.assertEqual(row['until'], datetime(2026, 10, 31, 18, 48))

    def test_waiting(self):
        row = build_rows({'Main': task_data()}, {}, now=NOW)[0]
        self.assertEqual(row['state'], 'waiting')
        row = build_rows({'Main': task_data()}, {}, running_task='Main', now=NOW)[0]
        self.assertEqual(row['state'], 'running')
        # NextRun passed, waiting for other tasks to finish
        row = build_rows({'Main': task_data(next_run='2026-10-31 17:30:00')}, {}, now=NOW)[0]
        self.assertEqual(row['state'], 'pending')

    def test_single_fleet(self):
        row = build_rows({'Main': task_data(fleet2=0, order='fleet1_all_fleet2_standby')}, {}, now=NOW)[0]
        self.assertEqual(len(row['fleets']), 1)
        self.assertEqual(row['fleets'][0]['role'], 'all')
        self.assertFalse(row['auto_swap'])

    def test_disabled(self):
        self.assertEqual(build_rows({'Main': task_data(enable=False)}, {}, now=NOW), [])

    def test_groups(self):
        # Real case: Main and Event use the same game fleets, shown as one group
        data = {'Main': task_data(value1=52), 'Event': task_data(name='T5', value1=99),
                'Main2': task_data(fleet2=0)}
        data['Main2']['Fleet']['Fleet1'] = 3
        data['Event']['Emotion']['Fleet1Record'] = '2026-10-31 17:00:00'
        status = {'Event': {'state': 'battle', 'order': 'fleet1_boss_fleet2_mob', 'fleet': 1},
                  '_EmotionSync': {'time': '2026-10-31 17:30:00', 'fleets': {
                      '1': {'value': 52, 'speed': 40, 'tab': 'training'},
                      '2': {'value': 80, 'speed': 50, 'tab': 'rest'}}}}
        groups = build_groups(data, status, running_task='Event', now=NOW)
        self.assertEqual(len(groups), 2)
        group = groups[0]
        self.assertEqual([row['task'] for row in group['tasks']], ['Main', 'Event'])
        # Fleets of running task, with the latest emotion record shared by both tasks
        self.assertEqual([f['role'] for f in group['fleets']], ['boss', 'mob'])
        self.assertEqual([f['fighting'] for f in group['fleets']], [True, False])
        self.assertEqual(group['fleets'][0]['value'], 52)
        self.assertEqual([f['speed'] for f in group['fleets']], [40, 20])
        self.assertEqual(group['synced'], datetime(2026, 10, 31, 17, 30))
        self.assertFalse(group['reversed'])
        self.assertEqual([f['number'] for f in groups[1]['fleets']], [3])
        self.assertIsNone(groups[1]['synced'])
        # Dorm floors reversed, shown on synced group only
        status['_EmotionSync']['reversed'] = True
        groups = build_groups(data, status, running_task='Event', now=NOW)
        self.assertTrue(groups[0]['reversed'])
        self.assertFalse(groups[1]['reversed'])
        # Group sharing only one of the synced fleets
        data['Main2']['Fleet']['Fleet1'] = 1
        groups = build_groups(data, status, running_task='Event', now=NOW)
        self.assertEqual(groups[1]['numbers'], [1])
        self.assertFalse(groups[1]['reversed'])
        # No sync time to date the warning
        status['_EmotionSync']['time'] = 'broken'
        self.assertFalse(build_groups(data, status, now=NOW)[0]['reversed'])

    def test_latest_emotion(self):
        data = {'Main': task_data(value1=52), 'Event': task_data(value1=99), 'Hard': {'Fleet': {'Fleet1': 1}},
                'GemsFarming': task_data(value1=150)}
        data['Event']['Emotion']['Fleet1Record'] = '2026-10-31 18:30:00'
        data['GemsFarming']['Emotion']['Fleet1Record'] = '2026-10-31 18:40:00'
        # GemsFarming changes ships in its fleet, not shared
        self.assertEqual(emotion_slots(data, 1), [('Main', 1), ('Event', 1)])
        self.assertEqual(emotion_slots(data, 1, exclude_task='Main'), [('Event', 1)])
        self.assertEqual(emotion_slots(data, 0), [])
        self.assertEqual(latest_emotion(data, 1), (99, datetime(2026, 10, 31, 18, 30)))
        self.assertEqual(latest_emotion(data, 1, exclude_task='Event')[0], 52)
        self.assertIsNone(latest_emotion(data, 5))

    def test_emotion_synced_recently(self):
        status = {'_EmotionSync': {'time': '2026-10-31 17:55:00', 'fleets': {'1': {}, '2': {}}}}
        self.assertTrue(emotion_synced_recently(status, [1, 2], 10, now=NOW))
        self.assertFalse(emotion_synced_recently(status, [1, 2], 3, now=NOW))
        self.assertFalse(emotion_synced_recently(status, [1, 3], 10, now=NOW))
        self.assertFalse(emotion_synced_recently(status, [], 10, now=NOW))
        self.assertFalse(emotion_synced_recently({}, [1], 10, now=NOW))
        # Sync time in the future
        self.assertFalse(emotion_synced_recently(status, [1], 10, now=datetime(2026, 10, 31, 17, 0)))

    def test_stage_name(self):
        # Real case: Campaign_Name is the map file name in Alas process
        status = {'Main': {'state': 'battle', 'stage': 'campaign_7_4', 'battle': 1, 'battle_total': 6}}
        row = build_rows({'Main': task_data()}, status, running_task='Main', now=NOW)[0]
        self.assertEqual(row['stage'], '7-4')
        self.assertEqual(stage_name('7-3'), '7-3')
        self.assertEqual(stage_name('t5'), 'T5')
        self.assertEqual(stage_name('campaign_10_1'), '10-1')

    def test_format_time(self):
        self.assertEqual(format_time(datetime(2026, 11, 1, 0, 5), now=NOW), '11-01 00:05')
        self.assertEqual(format_time(None, now=NOW), '')


if __name__ == '__main__':
    unittest.main()
