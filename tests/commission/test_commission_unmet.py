"""Tests for skipping commissions with unmet requirements, and limited task delay (no device interaction)."""
import json
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import numpy as np
from PIL import Image

import module.config.server as server
from module.base.resource import release_resources
from module.commission.commission import RewardCommission

HERE = os.path.dirname(os.path.abspath(__file__))
# Rows 220~300 of a JP screenshot, limitation row:
# "Lv10以上の艦1隻以上の必須 X" (red), "3隻以上必要 V" (green), "無制限 V" (green)
STRIP = os.path.join(HERE, 'commission_unmet_limitation_jp.png')
STRIP_Y = 220


def screenshot_with_strip(strip):
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[STRIP_Y:STRIP_Y + strip.shape[0]] = strip
    return image


class FakeDevice:
    image = None


class TestRequirementUnmet(unittest.TestCase):
    def setUp(self):
        self.server = server.server
        server.server = 'jp'
        release_resources()
        self.addCleanup(self.restore_server)
        self.strip = np.array(Image.open(STRIP).convert('RGB'))
        self.commission = RewardCommission.__new__(RewardCommission)
        self.commission.device = FakeDevice()

    def restore_server(self):
        server.server = self.server
        release_resources()

    def test_red_limitation_detected(self):
        self.commission.device.image = screenshot_with_strip(self.strip)
        self.assertTrue(self.commission._commission_requirement_unmet())

    def test_all_limitations_met(self):
        # Replace the unmet limitation with a met one (green), copied from the right side
        strip = self.strip.copy()
        strip[:, 320:590] = strip[:, 885:1155]
        self.commission.device.image = screenshot_with_strip(strip)
        self.assertFalse(self.commission._commission_requirement_unmet())


class FakeCommission:
    def __init__(self, name):
        self.name = name
        self.status = 'pending'

    def convert_to_running(self):
        self.status = 'running'

    def __repr__(self):
        return self.name


class TestStopOnUnmet(unittest.TestCase):
    def make(self, daily, urgent, unmet=()):
        """
        Args:
            daily (list[str]): Chosen daily commissions.
            urgent (list[str]): Chosen urgent commissions.
            unmet (tuple[str]): Commissions that no idle ships meet requirements.
        """
        c = RewardCommission.__new__(RewardCommission)
        c.tried = []
        comms = {name: FakeCommission(name) for name in daily + urgent}

        def scan_all():
            c.daily_choose = [comms[name] for name in daily]
            c.urgent_choose = [comms[name] for name in urgent]

        def find_and_start(comm, is_urgent=False):
            c.tried.append(comm.name)
            if comm.name in unmet:
                c.comm_requirement_unmet = True
                return False
            return True

        c._commission_scan_all = scan_all
        c._commission_find_and_start = find_and_start
        c._commission_ensure_mode = lambda mode: None
        c._commission_swipe_to_top = lambda: None
        c._commission_mode_reset = lambda: None
        c.handle_info_bar = lambda: None
        c.comms = comms
        return c

    def test_all_started(self):
        c = self.make(['A', 'B'], ['C'])
        c.commission_start()
        self.assertEqual(c.tried, ['A', 'B', 'C'])
        self.assertEqual([x.status for x in c.comms.values()], ['running'] * 3)

    def test_stop_after_first_unmet(self):
        # No idle ships left, other commissions won't do either
        c = self.make(['A', 'B'], ['C'], unmet=('A',))
        c.commission_start()
        self.assertEqual(c.tried, ['A'])

    def test_started_before_unmet_kept(self):
        c = self.make(['A', 'B'], ['C'], unmet=('B',))
        c.commission_start()
        self.assertEqual(c.tried, ['A', 'B'])
        self.assertEqual(c.comms['A'].status, 'running')
        self.assertEqual(c.comms['C'].status, 'pending')

    def test_flag_reset_each_run(self):
        c = self.make(['A'], [], unmet=('A',))
        c.commission_start()
        self.assertTrue(c.comm_requirement_unmet)
        c2 = self.make(['A'], [])
        c2.comm_requirement_unmet = True
        c2.commission_start()
        self.assertEqual(c2.tried, ['A'])


class TestTaskDelayLimit(unittest.TestCase):
    """Real AzurLaneConfig with a temporary config file."""
    name = 'zz_test_task_delay_limit'

    def setUp(self):
        from module.config.config import AzurLaneConfig
        from module.config.utils import filepath_config, read_file
        template = read_file(filepath_config('template'))
        self.path = filepath_config(self.name)
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(template, f, default=str)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))
        self.AzurLaneConfig = AzurLaneConfig

    def next_run(self, task):
        config = self.AzurLaneConfig(self.name, task=task)
        return config.cross_get(f'{task}.Scheduler.NextRun')

    def test_commission_delay_limited_to_12h(self):
        config = self.AzurLaneConfig(self.name, task='Commission')
        config.task_delay(target=datetime.now() + timedelta(hours=22))
        next_run = self.next_run('Commission')
        # Not reset to now by override(), and within 12 hours
        self.assertGreater(next_run, datetime.now() + timedelta(hours=11))
        self.assertLessEqual(next_run, datetime.now() + timedelta(hours=12))

    def test_other_task_delay_limited_to_24h(self):
        config = self.AzurLaneConfig(self.name, task='Main')
        config.task_delay(target=datetime.now() + timedelta(days=2))
        next_run = self.next_run('Main')
        self.assertGreater(next_run, datetime.now() + timedelta(hours=23))
        self.assertLessEqual(next_run, datetime.now() + timedelta(hours=24))

    def test_delay_within_limit_unchanged(self):
        config = self.AzurLaneConfig(self.name, task='Commission')
        target = (datetime.now() + timedelta(hours=3)).replace(microsecond=0)
        config.task_delay(target=target)
        self.assertEqual(self.next_run('Commission'), target)

    def test_limit_values(self):
        config = self.AzurLaneConfig(self.name, task='Main')
        self.assertEqual(config.next_run_limit('Reward'), timedelta(hours=12, seconds=-1))
        self.assertEqual(config.next_run_limit('Research'), timedelta(hours=24, seconds=-1))
        self.assertEqual(config.next_run_limit('OpsiArchive'), timedelta(days=7, seconds=-1))
        self.assertEqual(config.next_run_limit('OpsiExplore'), timedelta(days=31, seconds=-1))
        self.assertEqual(config.next_run_limit('Main'), timedelta(hours=24, seconds=-1))
        self.assertIsNone(config.next_run_limit('NotATask'))


if __name__ == '__main__':
    unittest.main()
