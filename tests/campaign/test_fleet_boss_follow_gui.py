"""Tests for main chapter maps with FLEET_BOSS = 1 following Fleet.FleetOrder (no device interaction)."""
import copy
import importlib
import json
import os
import unittest
from unittest.mock import patch

from module.campaign.run import CampaignRun
from module.config.config import AzurLaneConfig
from module.config.utils import filepath_config, read_file


class TestFleetBossFollowGui(unittest.TestCase):
    """Uses a temporary config file, see TestConfigBindAutoSwap."""
    name = 'zz_test_fleet_boss_follow_gui'

    def setUp(self):
        template = read_file(filepath_config('template'))
        template['Main']['Fleet']['Fleet1'] = 1
        template['Main']['Fleet']['Fleet2'] = 2
        template['Main']['Fleet']['FleetOrder'] = 'fleet1_mob_fleet2_boss'
        self.path = filepath_config(self.name)
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(template, f, default=str)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))

    def load(self, name, folder='campaign_main', **override):
        """
        Returns:
            AzurLaneConfig: Config of the map, as CampaignRun.load_campaign() does.
        """
        config = AzurLaneConfig(self.name, task='Main')
        if override:
            config.override(**override)
        run = CampaignRun.__new__(CampaignRun)
        run.folder = folder
        run.module = importlib.import_module('.' + name, f'campaign.{folder}')
        config = copy.deepcopy(config).merge(run.module.Config())
        run.fleet_boss_follow_gui(config)
        return config

    def test_map_file_fleet_boss_1_follows_gui(self):
        # Real case: 4-1, boss fought by mob fleet although fleet 2 is the boss fleet
        self.assertEqual(self.load('campaign_4_1').FLEET_BOSS, 2)
        # 4-2 imports Config from 4-1
        self.assertEqual(self.load('campaign_4_2').FLEET_BOSS, 2)
        self.assertEqual(self.load('campaign_6_4').FLEET_BOSS, 2)

    def test_reversed_order(self):
        # FLEET_BOSS is the fleet role, 2 for a separate boss fleet, slots are reversed by fleets_reversed
        config = self.load('campaign_4_1', Fleet_FleetOrder='fleet1_boss_fleet2_mob')
        self.assertEqual(config.FLEET_BOSS, 2)

    def test_follows_order_changed_later(self):
        # Fleet order is read again after map loaded, not fixed to the value at loading
        config = self.load('campaign_4_1')
        config.override(Fleet_FleetOrder='fleet1_all_fleet2_standby')
        self.assertEqual(config.FLEET_BOSS, 1)

    def test_single_fleet_unchanged(self):
        self.assertEqual(self.load('campaign_4_1', Fleet_Fleet2=0).FLEET_BOSS, 1)
        config = self.load('campaign_4_1', Fleet_FleetOrder='fleet1_all_fleet2_standby')
        self.assertEqual(config.FLEET_BOSS, 1)

    def test_load_campaign_calls_it(self):
        # Through the real call site, Campaign class replaced to skip device
        run = CampaignRun.__new__(CampaignRun)
        run.config = AzurLaneConfig(self.name, task='Main')
        run.device = None
        module = importlib.import_module('.campaign_5_1', 'campaign.campaign_main')
        with patch.object(module, 'Campaign', lambda config, device: config):
            self.assertTrue(run.load_campaign('campaign_5_1'))
        self.assertEqual(run.campaign.FLEET_BOSS, 2)

    def test_other_map_files_unchanged(self):
        # FLEET_BOSS = 2 in map file
        self.assertEqual(self.load('campaign_7_1', Fleet_FleetOrder='fleet1_boss_fleet2_mob').FLEET_BOSS, 2)
        # Event maps with FLEET_BOSS = 1 may allow fleet_1 only
        self.assertEqual(self.load('b1', folder='event_20200423_cn').FLEET_BOSS, 1)


if __name__ == '__main__':
    unittest.main()
