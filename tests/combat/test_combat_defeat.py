"""Tests for withdrawing from campaign after fleet defeated (no device interaction)."""
import os
import unittest

import numpy as np
from PIL import Image

import module.config.server as server
from module.base.resource import release_resources
from module.campaign.campaign_base import CampaignBase
from module.combat.combat import Combat
from module.config.config import TaskEnd
from module.exception import CampaignDefeat, CampaignEnd

HERE = os.path.dirname(os.path.abspath(__file__))
# Area (300, 40, 420, 125) of a JP exp info screen, "艦隊全滅 D"
STRIP = os.path.join(HERE, 'exp_info_d_jp.png')
STRIP_X, STRIP_Y = 300, 40


def screenshot_with_strip(strip):
    image = np.zeros((720, 1280, 3), dtype=np.uint8)
    image[STRIP_Y:STRIP_Y + strip.shape[0], STRIP_X:STRIP_X + strip.shape[1]] = strip
    return image


class FakeDevice:
    image = None

    def __init__(self):
        self.clicked = []

    def stuck_record_add(self, button):
        pass

    def stuck_record_clear(self):
        pass

    def click_record_clear(self):
        pass

    def screenshot_interval_set(self, *args, **kwargs):
        pass

    def click(self, button):
        self.clicked.append(button.name)

    def sleep(self, second):
        pass


class FakeConfig:
    SERVER = 'jp'
    Campaign_Name = '5-2'
    Campaign_Mode = 'normal'
    config_name = 'alas'
    Error_OnePushConfig = 'provider: null'

    def __init__(self):
        self.delay = None

    def task_delay(self, **kwargs):
        self.delay = kwargs

    @staticmethod
    def task_stop(message=''):
        raise TaskEnd(message)


class TestExpInfoDefeat(unittest.TestCase):
    def setUp(self):
        self.server = server.server
        server.server = 'jp'
        release_resources()
        self.addCleanup(self.restore_server)
        self.combat = Combat.__new__(Combat)
        self.combat.config = FakeConfig()
        self.combat.device = FakeDevice()
        self.combat.interval_timer = {}

    def restore_server(self):
        server.server = self.server
        release_resources()

    def test_defeat_detected(self):
        strip = np.array(Image.open(STRIP).convert('RGB'))
        self.combat.device.image = screenshot_with_strip(strip)
        self.assertTrue(self.combat.handle_exp_info_defeat())
        self.assertTrue(self.combat.combat_defeated)
        self.assertEqual(self.combat.device.clicked, ['EXP_INFO_D'])
        # Not a victory
        self.assertFalse(self.combat.handle_exp_info())

    def test_no_defeat(self):
        self.combat.device.image = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.assertFalse(self.combat.handle_exp_info_defeat())
        self.assertFalse(self.combat.combat_defeated)
        self.assertEqual(self.combat.device.clicked, [])


class FakeCombatStatus(Combat):
    """Combat with screenshots replaced by a list of states."""

    def __init__(self, states):
        """
        Args:
            states (list[str]): State of each screenshot, exp_info_d, in_map, in_stage.
        """
        self.config = FakeConfig()
        self.device = FakeDevice()
        self.states = states
        self.state = None

    def loop(self, skip_first=True, timeout=None):
        for state in self.states:
            self.state = state
            yield None
        raise AssertionError('Combat status not ended')

    def handle_exp_info_defeat(self):
        if self.state == 'exp_info_d':
            self.combat_defeated = True
            return True
        return False

    def is_in_map(self):
        return self.state == 'in_map'

    def is_in_stage(self):
        return self.state == 'in_stage'

    def handle_in_stage(self):
        if self.is_in_stage():
            raise CampaignEnd('In stage.')
        return False

    def handle_in_map_with_enemy_searching(self, drop=None):
        return self.is_in_map()

    def handle_in_map_no_enemy_searching(self, drop=None):
        return self.is_in_map()


def no_handle(self, *args, **kwargs):
    return False


for _name in ['handle_story_skip', 'handle_get_ship', 'handle_get_items', 'handle_popup_confirm',
              'handle_battle_status', 'handle_exp_info', 'handle_urgent_commission', 'handle_guild_popup_cancel',
              'handle_vote_popup', 'handle_mission_popup_ack', 'handle_auto_search_exit', 'handle_combat_mis_click']:
    setattr(FakeCombatStatus, _name, no_handle)


class TestCombatStatusDefeat(unittest.TestCase):
    def test_defeat_back_to_map(self):
        # Without defeat handling, with_searching would end combat status and continue the map
        combat = FakeCombatStatus(['battle', 'exp_info_d', 'in_map'])
        with self.assertRaises(CampaignDefeat):
            combat.combat_status(expected_end='with_searching')

    def test_all_fleets_defeated_back_to_stage(self):
        combat = FakeCombatStatus(['exp_info_d', 'in_stage'])
        with self.assertRaises(CampaignDefeat):
            combat.combat_status(expected_end='in_stage')

    def test_callable_expected_end_not_affected(self):
        # Combats outside map (raid, hospital, daily) end by their own expected_end
        combat = FakeCombatStatus(['exp_info_d', 'in_stage'])
        combat.combat_status(expected_end=lambda: combat.state == 'in_stage')
        self.assertTrue(combat.combat_defeated)

    def test_in_ui_expected_end_not_affected(self):
        # Guild boss combat uses expected_end='in_ui', which ends at BACK_ARROW
        combat = FakeCombatStatus(['exp_info_d', 'in_map', 'in_ui'])
        combat.appear = lambda button, *args, **kwargs: combat.state == 'in_ui'
        combat.combat_status(expected_end='in_ui')
        self.assertTrue(combat.combat_defeated)

    def test_victory_not_affected(self):
        combat = FakeCombatStatus(['battle', 'in_map'])
        combat.combat_status(expected_end='with_searching')
        self.assertFalse(combat.combat_defeated)

    def test_flag_reset_each_combat(self):
        combat = FakeCombatStatus(['in_map'])
        combat.combat_defeated = True
        combat.combat_status(expected_end='with_searching')
        self.assertFalse(combat.combat_defeated)


class FakeCampaign(CampaignBase):
    MAP = None
    class ENTRANCE:
        area = None
        button = None

    def __init__(self, in_map):
        self.config = FakeConfig()
        self.device = FakeDevice()
        self.in_map = in_map
        self.withdrawn = False
        self.battles = 0

    @property
    def emotion(self):
        class FakeEmotion:
            def check_reduce(self, battle):
                pass

        return FakeEmotion()

    def enter_map(self, *args, **kwargs):
        pass

    def handle_map_fleet_lock(self, *args, **kwargs):
        pass

    def map_init(self, *args, **kwargs):
        pass

    def execute_a_battle(self):
        self.battles += 1
        raise CampaignDefeat('Combat defeated')

    def is_in_map(self):
        return self.in_map

    def withdraw(self, skip_first_screenshot=True):
        self.withdrawn = True
        raise CampaignEnd('Withdraw')


class TestCampaignDefeat(unittest.TestCase):
    def test_withdraw_and_delay(self):
        campaign = FakeCampaign(in_map=True)
        campaign._map_battle = 5
        with self.assertRaises(TaskEnd):
            campaign.run()
        self.assertEqual(campaign.battles, 1)
        self.assertTrue(campaign.withdrawn)
        self.assertEqual(campaign.config.delay, {'minute': CampaignBase.DEFEAT_RETRY_MINUTE})

    def test_back_to_stage_no_withdraw(self):
        campaign = FakeCampaign(in_map=False)
        campaign._map_battle = 5
        with self.assertRaises(TaskEnd):
            campaign.run()
        self.assertFalse(campaign.withdrawn)
        self.assertEqual(campaign.config.delay, {'minute': 60})

    def test_defeat_is_campaign_end(self):
        # Callers who only catch CampaignEnd still end the campaign
        self.assertTrue(issubclass(CampaignDefeat, CampaignEnd))


if __name__ == '__main__':
    unittest.main()
