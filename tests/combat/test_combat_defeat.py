"""Tests for withdrawing from campaign after fleet defeated (no device interaction)."""
import os
import time
import unittest

import numpy as np
from PIL import Image

import module.config.server as server
from module.base.resource import release_resources
from module.base.timer import Timer
from module.campaign.campaign_base import CampaignBase
from module.combat.auto_search_combat import AutoSearchCombat
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
        self.combat.battle_status_defeated = True
        self.assertTrue(self.combat.handle_exp_info_defeat())
        self.assertTrue(self.combat.combat_defeated)
        self.assertEqual(self.combat.device.clicked, ['EXP_INFO_D'])
        # Not a victory
        self.assertFalse(self.combat.handle_exp_info())

    def test_exp_info_c_after_victory_ignored(self):
        # EXP_INFO_C is a color check, it matched the GET_SHIP screen after a battle status S.
        # Defeat must not be handled, otherwise get ship is no longer handled and combat status stucks.
        from module.combat.assets import EXP_INFO_C
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        x1, y1, x2, y2 = EXP_INFO_C.area
        image[y1:y2, x1:x2] = EXP_INFO_C.color
        self.combat.device.image = image
        self.assertTrue(EXP_INFO_C.appear_on(image))
        self.assertFalse(self.combat.handle_exp_info_defeat())
        self.assertFalse(self.combat.combat_defeated)
        self.assertEqual(self.combat.device.clicked, [])

    def test_no_defeat(self):
        self.combat.device.image = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.combat.battle_status_defeated = True
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
              'handle_vote_popup', 'handle_mission_popup_ack', 'handle_auto_search_exit', 'handle_combat_mis_click',
              'handle_combat_defeat_tips']:
    setattr(FakeCombatStatus, _name, no_handle)


class FakeCombatStatusBattle(FakeCombatStatus):
    """Combat status with battle status clicked."""
    IN_STAGE_MISSING_SECOND = 0.02

    def loop(self, skip_first=True, timeout=None):
        for _ in super().loop(skip_first=skip_first, timeout=timeout):
            # Timer reaches on the second frame in map, never on the first one.
            # Limit is above time() resolution (about 16ms on Windows), and sleep is above the limit
            time.sleep(0.03)
            yield None

    def handle_battle_status(self, drop=None):
        return self.state == 'battle'


class TestCombatStatusInStageMissing(unittest.TestCase):
    def test_boss_intercepted_back_in_map(self):
        # Moving siren intercepted the fleet walking to boss, game is back in map
        combat = FakeCombatStatusBattle(['battle'] + ['in_map'] * 3)
        combat.combat_status(expected_end='in_stage')
        self.assertEqual(combat.state, 'in_map')

    def test_boss_in_stage(self):
        combat = FakeCombatStatusBattle(['battle', 'in_map', 'in_stage'])
        with self.assertRaises(CampaignEnd):
            combat.combat_status(expected_end='in_stage')

    def test_in_map_timer_reset_by_other_screen(self):
        combat = FakeCombatStatusBattle(['battle', 'in_map', 'battle', 'in_map', 'in_stage'])
        with self.assertRaises(CampaignEnd):
            combat.combat_status(expected_end='in_stage')

    def test_in_map_before_battle_status(self):
        # In map before battle status is not the end of combat
        combat = FakeCombatStatusBattle(['in_map'] * 3 + ['battle', 'in_stage'])
        with self.assertRaises(CampaignEnd):
            combat.combat_status(expected_end='in_stage')

    def test_waits_a_few_seconds(self):
        combat = FakeCombatStatusBattle(['battle'] + ['in_map'] * 3 + ['in_stage'])
        combat.IN_STAGE_MISSING_SECOND = 5
        with self.assertRaises(CampaignEnd):
            combat.combat_status(expected_end='in_stage')

    def test_defeated_back_in_map(self):
        combat = FakeCombatStatusBattle(['battle', 'exp_info_d', 'in_map'])
        with self.assertRaises(CampaignDefeat):
            combat.combat_status(expected_end='in_stage')


class FakeBattleStatus(Combat):
    """handle_battle_status() with appear() replaced by a set of appearing buttons."""

    def __init__(self):
        self.config = FakeConfig()
        self.device = FakeDevice()
        self.appearing = set()

    def is_combat_executing(self):
        return 'PAUSE' in self.appearing

    def appear(self, button, offset=0, interval=0, similarity=0.85, threshold=10):
        return button.name in self.appearing

    def battle(self, *names):
        self.appearing = set(names)
        return self.handle_battle_status()


class TestBattleStatusAfterWon(unittest.TestCase):
    def test_get_ship_misread_as_d(self):
        # Real case: CN, BATTLE_STATUS_S clicked, then GET_SHIP matched BATTLE_STATUS_D
        combat = FakeBattleStatus()
        self.assertTrue(combat.battle('BATTLE_STATUS_S'))
        self.assertFalse(combat.battle('BATTLE_STATUS_D', 'GET_SHIP'))
        self.assertFalse(combat.battle('BATTLE_STATUS_C'))
        self.assertFalse(combat.battle_status_defeated)
        self.assertEqual(combat.device.clicked, ['BATTLE_STATUS_S'])

    def test_defeated(self):
        combat = FakeBattleStatus()
        self.assertTrue(combat.battle('BATTLE_STATUS_D'))
        self.assertTrue(combat.battle_status_defeated)

    def get_ship(self, combat):
        combat.appearing = {'GET_SHIP'}
        return combat.handle_get_ship()

    def test_get_ship_without_battle_status(self):
        # Real case: JP auto search skipped battle status, GET_SHIP clicked, then it matched BATTLE_STATUS_C
        combat = FakeBattleStatus()
        self.assertTrue(self.get_ship(combat))
        self.assertTrue(combat.battle_status_won)
        self.assertFalse(combat.battle('BATTLE_STATUS_C'))
        self.assertFalse(combat.battle_status_defeated)

    def test_get_ship_after_misread_defeat(self):
        # GET_SHIP matched BATTLE_STATUS_C and EXP_INFO_D before the GET_SHIP template
        combat = FakeBattleStatus()
        self.assertTrue(combat.battle('BATTLE_STATUS_C'))
        combat.combat_defeated = True
        self.assertTrue(self.get_ship(combat))
        self.assertFalse(combat.battle_status_defeated)
        self.assertFalse(combat.combat_defeated)
        self.assertFalse(combat.battle('BATTLE_STATUS_D'))

    def test_combat_status_resets(self):
        combat = FakeCombatStatusBattle(['in_map'])
        combat.battle_status_won = True
        combat.battle_status_grade = 'S'
        combat.combat_status(expected_end='no_searching')
        self.assertFalse(combat.battle_status_won)
        self.assertIsNone(combat.battle_status_grade)


class TestExpInfoGrade(unittest.TestCase):
    def exp_info(self, combat, *names):
        combat.appearing = set(names)
        return combat.handle_exp_info()

    def test_get_ship_misread_as_exp_info_b(self):
        # Real case: JP, BATTLE_STATUS_S clicked, then GET_SHIP animation matched EXP_INFO_B.
        # exp_info=True disabled handle_get_ship() and combat status stuck on the new ship screen.
        combat = FakeBattleStatus()
        self.assertTrue(combat.battle('BATTLE_STATUS_S'))
        self.assertEqual(combat.battle_status_grade, 'S')
        self.assertFalse(self.exp_info(combat, 'EXP_INFO_B', 'GET_SHIP'))
        self.assertFalse(self.exp_info(combat, 'EXP_INFO_A'))
        self.assertTrue(self.exp_info(combat, 'EXP_INFO_S'))
        self.assertEqual(combat.device.clicked, ['BATTLE_STATUS_S', 'EXP_INFO_S'])

    def test_later_battle_status_misread_ignored(self):
        # Real case: JP, BATTLE_STATUS_S, BATTLE_STATUS_S, BATTLE_STATUS_B, then EXP_INFO_S
        combat = FakeBattleStatus()
        self.assertTrue(combat.battle('BATTLE_STATUS_S'))
        self.assertTrue(combat.battle('BATTLE_STATUS_B'))
        self.assertEqual(combat.battle_status_grade, 'S')
        self.assertTrue(self.exp_info(combat, 'EXP_INFO_S'))

    def fast_timer(self, combat):
        combat._exp_info_grade_timer = Timer(0.01)

    def test_misread_battle_status_falls_back(self):
        # First battle status misread as S, but real exp info is A and stays
        combat = FakeBattleStatus()
        self.fast_timer(combat)
        self.assertTrue(combat.battle('BATTLE_STATUS_S'))
        self.assertFalse(self.exp_info(combat, 'EXP_INFO_A'))
        time.sleep(0.03)
        self.assertTrue(self.exp_info(combat, 'EXP_INFO_A'))
        self.assertEqual(combat.device.clicked, ['BATTLE_STATUS_S', 'EXP_INFO_A'])
        self.assertIsNone(combat.battle_status_grade)

    def test_flashing_misread_not_accepted(self):
        # Misread on animation frames is not continuous, timer restarts
        combat = FakeBattleStatus()
        self.fast_timer(combat)
        self.assertTrue(combat.battle('BATTLE_STATUS_S'))
        for _ in range(3):
            self.assertFalse(self.exp_info(combat, 'EXP_INFO_B'))
            self.assertFalse(self.exp_info(combat, 'GET_SHIP'))
            time.sleep(0.03)
        self.assertEqual(combat.device.clicked, ['BATTLE_STATUS_S'])

    def test_timer_not_shared_and_reset(self):
        # A started timer must not make another combat or the next battle accept a misread at once
        first = FakeBattleStatus()
        first.battle('BATTLE_STATUS_S')
        self.exp_info(first, 'EXP_INFO_B')
        self.assertTrue(first._exp_info_grade_timer.started())
        second = FakeBattleStatus()
        self.assertFalse(second._exp_info_grade_timer.started())
        first.battle_status_reset()
        self.assertFalse(first._exp_info_grade_timer.started())
        self.assertIsNone(first.battle_status_grade)

    def test_grade_a_and_b(self):
        for grade in ['A', 'B']:
            combat = FakeBattleStatus()
            self.assertTrue(combat.battle(f'BATTLE_STATUS_{grade}'))
            self.assertFalse(self.exp_info(combat, 'EXP_INFO_S'))
            self.assertTrue(self.exp_info(combat, f'EXP_INFO_{grade}'))

    def test_unknown_grade_accepts_all(self):
        # Battle status not seen, such as the white screen bug in game
        for grade in ['S', 'A', 'B']:
            combat = FakeBattleStatus()
            self.assertTrue(self.exp_info(combat, f'EXP_INFO_{grade}'))


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


class TestDefeatTips(unittest.TestCase):
    """Tips page "DEFEAT" after fleet defeated in auto search."""
    # Area (100, 250, 270, 420) of a JP defeat tips screen
    CROP = os.path.join(HERE, 'defeat_tips_jp.png')

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

    def screenshot(self):
        image = np.zeros((720, 1280, 3), dtype=np.uint8)
        image[250:420, 100:270] = np.array(Image.open(self.CROP).convert('RGB'))
        return image

    def test_defeat_tips_detected(self):
        self.combat.device.image = self.screenshot()
        self.assertTrue(self.combat.handle_combat_defeat_tips())
        self.assertTrue(self.combat.combat_defeated)
        # Exp info C or D after tips can be handled
        self.assertTrue(self.combat.battle_status_defeated)
        self.assertEqual(self.combat.device.clicked, ['DEFEAT_TIPS'])

    def test_click_back(self):
        from module.combat.assets import DEFEAT_TIPS
        # Click "back" at the bottom, not the shortcuts to other pages
        x1, y1, x2, y2 = DEFEAT_TIPS.button
        self.assertGreater(y1, 560)
        self.assertTrue(500 < x1 < x2 < 760)

    def test_no_defeat_tips(self):
        self.combat.device.image = np.zeros((720, 1280, 3), dtype=np.uint8)
        self.assertFalse(self.combat.handle_combat_defeat_tips())
        self.assertFalse(self.combat.combat_defeated)
        self.assertEqual(self.combat.device.clicked, [])


class FakeAutoSearchStatus(AutoSearchCombat):
    """AutoSearchCombat with screenshots replaced by a list of states."""

    def __init__(self, states):
        """
        Args:
            states (list[str]): State of each screenshot,
                battle_status_d, exp_info_d, defeat_tips, auto_search_running, auto_search_menu, in_map, in_stage.
        """
        self.config = FakeConfig()
        self.device = FakeDevice()
        self.states = states
        self.state = None
        self.combat_defeated = False
        self.battle_status_defeated = False
        self._auto_search_status_confirm = False
        self.handled = []

    def loop(self, skip_first=True, timeout=None):
        for state in self.states:
            self.state = state
            yield None
        raise AssertionError('Auto search combat status not ended')

    def appear(self, button, *args, **kwargs):
        return self.state == button.name.lower()

    def handle_battle_status(self, drop=None):
        if self.state == 'battle_status_d':
            self.battle_status_defeated = True
            self.handled.append(self.state)
            return True
        return False

    def handle_exp_info_defeat(self):
        if self.battle_status_defeated and self.state == 'exp_info_d':
            self.combat_defeated = True
            self.handled.append(self.state)
            return True
        return False

    def handle_combat_defeat_tips(self):
        if self.state == 'defeat_tips':
            self.combat_defeated = True
            self.battle_status_defeated = True
            self.handled.append(self.state)
            return True
        return False

    def is_auto_search_running(self):
        return self.state == 'auto_search_running'

    def is_in_auto_search_menu(self):
        return self.state == 'auto_search_menu'

    def is_in_map(self):
        return self.state in ['in_map', 'auto_search_running']

    def is_in_stage(self):
        return self.state == 'in_stage'


for _name in ['_handle_auto_search_menu_missing', 'handle_get_ship', 'handle_auto_search_map_option',
              'handle_popup_confirm', 'handle_urgent_commission', 'handle_story_skip', 'handle_guild_popup_cancel',
              'handle_vote_popup', 'handle_mission_popup_ack', 'handle_get_items', 'handle_exp_info']:
    setattr(FakeAutoSearchStatus, _name, no_handle)


class TestAutoSearchDefeat(unittest.TestCase):
    def test_defeat_tips_then_auto_search_continues(self):
        # Real case: game skips battle status and shows tips, the other fleet may continue auto search
        combat = FakeAutoSearchStatus(['defeat_tips', 'auto_search_running'])
        with self.assertRaises(CampaignDefeat):
            combat.auto_search_combat_status()
        self.assertEqual(combat.handled, ['defeat_tips'])

    def test_all_defeated_back_to_auto_search_menu(self):
        combat = FakeAutoSearchStatus(['battle_status_d', 'exp_info_d', 'defeat_tips', 'auto_search_menu'])
        with self.assertRaises(CampaignDefeat):
            combat.auto_search_combat_status()
        self.assertEqual(combat.handled, ['battle_status_d', 'exp_info_d', 'defeat_tips'])

    def test_defeat_back_to_stage(self):
        combat = FakeAutoSearchStatus(['battle_status_d', 'exp_info_d', 'in_stage'])
        with self.assertRaises(CampaignDefeat):
            combat.auto_search_combat_status()

    def test_victory_not_affected(self):
        combat = FakeAutoSearchStatus(['battle', 'auto_search_running'])
        combat.auto_search_combat_status()
        self.assertFalse(combat.combat_defeated)

    def test_victory_auto_search_end(self):
        combat = FakeAutoSearchStatus(['auto_search_menu'])
        with self.assertRaises(CampaignEnd) as e:
            combat.auto_search_combat_status()
        self.assertNotIsInstance(e.exception, CampaignDefeat)

    def test_execute_ends_on_defeat(self):
        # Combat execute waits for battle status S/A/B only, it was stuck on defeat tips
        for state in ['battle_status_d', 'defeat_tips']:
            combat = FakeAutoSearchStatus(['loading', 'pause', state])
            combat.is_combat_executing = lambda c=combat: 'PAUSE' if c.state == 'pause' else False
            combat.is_combat_loading = lambda: False
            combat.device.screenshot_interval_set = lambda *args: None
            combat.config.Submarine_Fleet = 0
            combat.config.Fleet_Fleet1Mode = 'combat_auto'
            for name in ['handle_combat_automation_confirm', 'handle_submarine_call', 'handle_combat_auto',
                         'handle_combat_manual', 'auto_search_combat_end']:
                setattr(combat, name, lambda *args, **kwargs: False)
            combat.submarine_call_reset = combat.combat_auto_reset = combat.combat_manual_reset = lambda: None
            combat.auto_search_combat_execute(emotion_reduce=False, fleet_index=1)
            self.assertEqual(combat.state, state)


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

    def ensure_auto_search_exit(self, skip_first_screenshot=True):
        self.auto_search_exited = True
        return False

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
        # All fleets defeated in auto search, auto search menu is closed
        self.assertTrue(campaign.auto_search_exited)
        self.assertEqual(campaign.config.delay, {'minute': 60})

    def test_defeat_is_campaign_end(self):
        # Callers who only catch CampaignEnd still end the campaign
        self.assertTrue(issubclass(CampaignDefeat, CampaignEnd))


if __name__ == '__main__':
    unittest.main()
