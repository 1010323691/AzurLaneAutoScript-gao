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

    def __eq__(self, other):
        return isinstance(other, FakeCommission) and self.name == other.name

    def __repr__(self):
        return self.name


class TestChooseAgain(unittest.TestCase):
    def make(self, unmet_rounds):
        """
        Args:
            unmet_rounds (list[list[str]]): Commissions found unmet in each round.
        """
        c = RewardCommission.__new__(RewardCommission)
        c.rounds = 0
        rounds = list(unmet_rounds)

        def start_chosen():
            c.rounds += 1
            if rounds:
                for name in rounds.pop(0):
                    c.comm_unmet.append(FakeCommission(name))

        c._commission_scan_all = lambda: None
        c._commission_start_chosen = start_chosen
        return c

    def test_no_unmet_runs_once(self):
        c = self.make([[]])
        c.commission_start()
        self.assertEqual(c.rounds, 1)

    def test_unmet_chooses_again(self):
        c = self.make([['A'], []])
        c.commission_start()
        self.assertEqual(c.rounds, 2)
        self.assertTrue(c._commission_is_unmet(FakeCommission('A')))
        self.assertFalse(c._commission_is_unmet(FakeCommission('B')))

    def test_choose_again_at_most_3_rounds(self):
        c = self.make([['A'], ['B'], ['C'], ['D']])
        c.commission_start()
        self.assertEqual(c.rounds, 3)

    def test_unmet_reset_each_run(self):
        c = self.make([['A'], []])
        c.commission_start()
        c.rounds = 0
        c._commission_start_chosen = lambda: None
        c.commission_start()
        self.assertFalse(c._commission_is_unmet(FakeCommission('A')))


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
