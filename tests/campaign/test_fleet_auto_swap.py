"""Tests for Fleet.FleetOrder=fleet1_mob_fleet2_boss_auto_swap (no device interaction)."""
import json
import os
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from module.combat.emotion import Emotion
from module.exception import ScriptEnd
from module.handler.auto_search import AutoSearchHandler

AUTO = 'fleet1_mob_fleet2_boss_auto_swap'


class FakeConfig:
    def __init__(self, v1, v2, auto_swap=True, order='fleet1_mob_fleet2_boss', fleet2=2,
                 mode='calculate', control='prevent_green_face'):
        now = datetime.now()
        self.FLEET_ORDER_AUTO_SWAP = auto_swap
        self.Fleet_FleetOrder = order
        self.Fleet_Fleet2 = fleet2
        self.Emotion_Mode = mode
        self.Campaign_Use2xBook = False
        for i, v in [(1, v1), (2, v2)]:
            setattr(self, f'Emotion_Fleet{i}Value', v)
            setattr(self, f'Emotion_Fleet{i}Record', now)
            setattr(self, f'Emotion_Fleet{i}Control', control)
            setattr(self, f'Emotion_Fleet{i}Recover', 'not_in_dormitory')
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
    2-2 has 4 battles: mob fleet 3 battles (6 emotion), boss fleet 1 battle (2 emotion).

    Returns:
        str: 'enter' or 'delay'
    """
    try:
        Emotion(config).check_reduce(battle)
        return 'enter'
    except ScriptEnd:
        return 'delay'


class TestEmotionAutoSwap(unittest.TestCase):
    def test_both_healthy_keeps_order(self):
        c = FakeConfig(119, 119)
        self.assertEqual(check(c), 'enter')
        self.assertEqual(c.Fleet_FleetOrder, 'fleet1_mob_fleet2_boss')

    def test_mob_fleet_low_swaps_without_waiting(self):
        c = FakeConfig(41, 119)
        self.assertEqual(check(c), 'enter')
        self.assertEqual(c.delays, [])
        self.assertEqual(c.Fleet_FleetOrder, 'fleet1_boss_fleet2_mob')

    def test_swapped_order_is_sticky(self):
        c = FakeConfig(41, 60, order='fleet1_boss_fleet2_mob')
        self.assertEqual(check(c), 'enter')
        self.assertEqual(c.Fleet_FleetOrder, 'fleet1_boss_fleet2_mob')

    def test_swap_back(self):
        c = FakeConfig(100, 43, order='fleet1_boss_fleet2_mob')
        self.assertEqual(check(c), 'enter')
        self.assertEqual(c.Fleet_FleetOrder, 'fleet1_mob_fleet2_boss')

    def test_tired_boss_fleet_relaxed_when_nothing_else_works(self):
        # Fleet 2 at 41 can't clear mobs and fails the strict boss check (needs 43),
        # relaxed check lets it fight boss as long as it's above 40
        c = FakeConfig(119, 41)
        self.assertEqual(check(c), 'enter')
        self.assertEqual(c.Fleet_FleetOrder, 'fleet1_mob_fleet2_boss')

    def test_keep_exp_bonus_not_relaxed(self):
        # Fleet 1 can clear mobs (130 > 126), boss fleet at 121 would drop below 120 after boss battle,
        # relaxing would let it enter, keep_exp_bonus must not be relaxed
        c = FakeConfig(130, 121, control='keep_exp_bonus')
        c.Emotion_Fleet1Recover = c.Emotion_Fleet2Recover = 'dormitory_floor_2'
        self.assertEqual(check(c), 'delay')

    def test_both_low_delays_to_earliest(self):
        c = FakeConfig(41, 44)
        c2 = FakeConfig(39, 39)
        self.assertEqual(check(c), 'delay')
        self.assertEqual(check(c2), 'delay')
        self.assertGreater(c.delays[0]['target'], datetime.now())
        self.assertLessEqual(c.delays[0]['target'], c2.delays[0]['target'])

    def test_not_auto_swap_keeps_original_behavior(self):
        c = FakeConfig(41, 119, auto_swap=False)
        self.assertEqual(check(c), 'delay')
        self.assertEqual(c.Fleet_FleetOrder, 'fleet1_mob_fleet2_boss')

    def test_no_fleet2_never_swaps(self):
        c = FakeConfig(41, 119, fleet2=0)
        self.assertEqual(check(c), 'delay')
        self.assertEqual(c.Fleet_FleetOrder, 'fleet1_mob_fleet2_boss')

    def test_other_order_never_swaps(self):
        # Such as hard mode overriding fleet order
        c = FakeConfig(119, 41, order='fleet1_standby_fleet2_all')
        self.assertEqual(check(c), 'delay')
        self.assertEqual(c.Fleet_FleetOrder, 'fleet1_standby_fleet2_all')

    def test_emotion_ignore(self):
        c = FakeConfig(0, 0, mode='ignore')
        self.assertEqual(check(c), 'enter')
        self.assertEqual(c.Fleet_FleetOrder, 'fleet1_mob_fleet2_boss')


class FakeCampaignConfig(FakeConfig):
    MAP_CLEAR_ALL_THIS_TIME = True
    FLEET_BOSS = 1


class FakeClearAllCampaign:
    """CampaignBase.handle_clear_all_emotion() with fake map and emotion."""
    from module.campaign.campaign_base import CampaignBase
    handle_clear_all_emotion = CampaignBase.handle_clear_all_emotion
    _map_battle_clear_all = CampaignBase._map_battle_clear_all

    class MAP:
        # spawn_data of campaign_3_4, Config inherits FLEET_BOSS = 1 from campaign_3_1
        _spawn_data = [
            {'battle': 0, 'enemy': 2},
            {'battle': 1, 'enemy': 2},
            {'battle': 2, 'enemy': 2},
            {'battle': 3, 'enemy': 1, 'boss': 1},
        ]
        spawn_data_loop = [
            {'battle': 0, 'enemy': 1},
            {'battle': 1, 'boss': 1},
        ]
        # Loop data left by previous run in clear mode
        spawn_data = spawn_data_loop

    map_is_clear_mode = False

    def __init__(self, config):
        self.config = config
        self.emotion = Emotion(config)
        self.cancelled = False

    def enter_map_cancel(self):
        self.cancelled = True

    def clear_all(self):
        """
        Returns:
            str: 'enter' or 'delay'
        """
        try:
            self.handle_clear_all_emotion()
            return 'enter'
        except ScriptEnd:
            return 'delay'


class TestClearAllEmotion(unittest.TestCase):
    def test_battle_count(self):
        campaign = FakeClearAllCampaign(FakeCampaignConfig(119, 119))
        # 7 enemies + 1 boss, plus boss battle counted in mob fleet when FLEET_BOSS = 1
        self.assertEqual(campaign._map_battle_clear_all, 9)

    def test_battle_count_ignores_previous_loop_data(self):
        campaign = FakeClearAllCampaign(FakeCampaignConfig(119, 119))
        self.assertEqual(campaign._map_battle_clear_all, 9)
        campaign.map_is_clear_mode = True
        self.assertEqual(campaign._map_battle_clear_all, 3)

    def test_swap_when_clear_all(self):
        # 3-4 in clear mode: fleet 1 has 51, enough for battles until boss (6), not for clearing all (16)
        c = FakeCampaignConfig(51, 117)
        self.assertEqual(check(c, battle=4), 'enter')
        self.assertEqual(c.Fleet_FleetOrder, 'fleet1_mob_fleet2_boss')
        FakeClearAllCampaign(c).handle_clear_all_emotion()
        self.assertEqual(c.Fleet_FleetOrder, 'fleet1_boss_fleet2_mob')
        self.assertEqual(c.delays, [])

    def test_both_low_delays_without_entering(self):
        # Real case: 6-1 to get 3 stars, 10 battles, fleet 1 has 44 and fleet 2 has 50, control limit 40.
        # Entered map and waited in map before, now cancel map preparation and delay
        c = FakeCampaignConfig(44, 50)
        c.FLEET_BOSS = 2
        campaign = FakeClearAllCampaign(c)
        campaign.MAP = type('MAP', (), {'_spawn_data': [{'battle': i, 'enemy': 2} for i in range(4)]
                                                       + [{'battle': 4, 'enemy': 1, 'boss': 1}]})
        self.assertEqual(campaign._map_battle_clear_all, 10)
        self.assertEqual(campaign.clear_all(), 'delay')
        self.assertTrue(campaign.cancelled)
        self.assertEqual(c.Fleet_FleetOrder, 'fleet1_mob_fleet2_boss')
        self.assertEqual(len(c.delays), 1)
        # Swapped, fleet 2 as mob fleet needs 18 + 40, recovers 2 per 6 min when not in dormitory: 4 * 6 min
        recovered = c.delays[0]['target']
        self.assertTrue(datetime.now() + timedelta(minutes=23) <= recovered <= datetime.now() + timedelta(minutes=30))

    def test_enough_enters(self):
        c = FakeCampaignConfig(58, 50)
        campaign = FakeClearAllCampaign(c)
        self.assertEqual(campaign.clear_all(), 'enter')
        self.assertFalse(campaign.cancelled)
        self.assertEqual(c.delays, [])

    def test_never_enough_waits_in_map(self):
        # 2x book, 21 battles = 80 emotion for mob fleet, control limit 40 + 80 is above max 119 of docks
        c = FakeCampaignConfig(119, 119, control='prevent_green_face')
        c.Campaign_Use2xBook = True
        campaign = FakeClearAllCampaign(c)
        campaign.MAP = type('MAP', (), {'_spawn_data': [{'battle': i, 'enemy': 5} for i in range(4)]})
        self.assertEqual(campaign._map_battle_clear_all, 21)
        self.assertEqual(campaign.clear_all(), 'enter')
        self.assertFalse(campaign.cancelled)
        self.assertEqual(c.delays, [])

    def test_delay_ignores_capped_order(self):
        # Fleet 1 in docks (max 119) can never have 40 + 80, fleet 2 in dormitory (max 150) can.
        # Delay until fleet 2 recovers, not the earlier time calculated for the capped fleet 1
        c = FakeCampaignConfig(110, 60)
        c.Emotion_Fleet2Recover = 'dormitory_floor_2'
        c.Campaign_Use2xBook = True
        campaign = FakeClearAllCampaign(c)
        campaign.MAP = type('MAP', (), {'_spawn_data': [{'battle': i, 'enemy': 5} for i in range(4)]})
        self.assertEqual(campaign.clear_all(), 'delay')
        self.assertEqual(c.Fleet_FleetOrder, 'fleet1_mob_fleet2_boss')
        # Fleet 2 needs 40 + 80 - 60 = 60, 5 per 6 min in floor 2: 12 * 6 min
        recovered = c.delays[0]['target']
        self.assertTrue(datetime.now() + timedelta(minutes=71) <= recovered <= datetime.now() + timedelta(minutes=78))

    def test_not_calculate(self):
        c = FakeCampaignConfig(44, 50, mode='ignore')
        campaign = FakeClearAllCampaign(c)
        self.assertEqual(campaign.clear_all(), 'enter')
        self.assertEqual(c.delays, [])

    def test_not_clear_all(self):
        c = FakeCampaignConfig(51, 117)
        c.MAP_CLEAR_ALL_THIS_TIME = False
        FakeClearAllCampaign(c).handle_clear_all_emotion()
        self.assertEqual(c.Fleet_FleetOrder, 'fleet1_mob_fleet2_boss')

    def test_emotion_overriding_check_reduce(self):
        # GemsEmotion overrides check_reduce(self, battle) without delay
        class GemsLikeEmotion(Emotion):
            def check_reduce(self, battle):
                raise AssertionError('Overridden check_reduce should not be called')

        c = FakeCampaignConfig(51, 117)
        campaign = FakeClearAllCampaign(c)
        campaign.emotion = GemsLikeEmotion(c)
        campaign.handle_clear_all_emotion()
        self.assertEqual(c.Fleet_FleetOrder, 'fleet1_boss_fleet2_mob')

    def test_not_auto_swap(self):
        # Not swapped, but still delayed if mob fleet is not enough for clearing all
        c = FakeCampaignConfig(51, 117, auto_swap=False)
        campaign = FakeClearAllCampaign(c)
        self.assertEqual(campaign.clear_all(), 'delay')
        self.assertTrue(campaign.cancelled)
        self.assertEqual(c.Fleet_FleetOrder, 'fleet1_mob_fleet2_boss')
        c = FakeCampaignConfig(57, 117, auto_swap=False)
        self.assertEqual(FakeClearAllCampaign(c).clear_all(), 'enter')


class TestConfigBindAutoSwap(unittest.TestCase):
    """AzurLaneConfig.bind() resolves the pseudo fleet order, using a temporary config file."""
    name = 'zz_test_fleet_auto_swap'

    def setUp(self):
        from module.config.config import AzurLaneConfig
        from module.config.utils import filepath_config, read_file
        template = read_file(filepath_config('template'))
        template['Main']['Fleet']['FleetOrder'] = AUTO
        self.path = filepath_config(self.name)
        with open(self.path, 'w', encoding='utf-8') as f:
            json.dump(template, f, default=str)
        self.addCleanup(lambda: os.path.exists(self.path) and os.remove(self.path))
        self.AzurLaneConfig = AzurLaneConfig

    def test_bind_resolves_pseudo_order(self):
        config = self.AzurLaneConfig(self.name, task='Main')
        self.assertTrue(config.FLEET_ORDER_AUTO_SWAP)
        self.assertEqual(config.Fleet_FleetOrder, 'fleet1_mob_fleet2_boss')
        self.assertEqual(config.FLEET_BOSS, 2)

    def test_bind_keeps_swapped_override(self):
        config = self.AzurLaneConfig(self.name, task='Main')
        config.override(Fleet_FleetOrder='fleet1_boss_fleet2_mob')
        config.bind('Main')
        self.assertTrue(config.FLEET_ORDER_AUTO_SWAP)
        self.assertEqual(config.Fleet_FleetOrder, 'fleet1_boss_fleet2_mob')

    def test_other_task_not_auto_swap(self):
        config = self.AzurLaneConfig(self.name, task='Main2')
        self.assertFalse(config.FLEET_ORDER_AUTO_SWAP)


class FakeGrid:
    def __getitem__(self, item):
        return item


class FakeSidebarDevice:
    def __init__(self, handler):
        self.handler = handler
        self.clicks = []

    def click(self, button):
        self.clicks.append(button)
        _, index = button
        if index + 1 <= self.handler.tabs:
            self.handler.current = index + 1
        else:
            # Clicking outside sidebar closes fleet preparation
            self.handler.closed = True


class FakeSidebarHandler(AutoSearchHandler):
    def __init__(self, tabs, current=1):
        self.tabs = tabs
        self.current = current
        self.closed = False
        self.device = FakeSidebarDevice(self)

    def loop(self, skip_first=True, timeout=None):
        for _ in range(10):
            yield None

    def _fleet_sidebar(self):
        return FakeGrid()

    def _fleet_preparation_get(self):
        if self.closed:
            self._fleet_sidebar_total = 0
            return 0
        self._fleet_sidebar_total = self.tabs
        return self.current


class TestFleetSidebar(unittest.TestCase):
    def setUp(self):
        # Timer(1, count=2) in sidebar ensure, let every check reach immediately
        p = patch('module.handler.auto_search.Timer')
        timer = p.start()
        timer.return_value.reached.return_value = True
        self.addCleanup(p.stop)

    def test_three_tabs(self):
        h = FakeSidebarHandler(tabs=3)
        self.assertTrue(h.fleet_preparation_sidebar_ensure(3))
        self.assertEqual(h.current, 3)

    def test_two_tabs_without_meowfficer(self):
        h = FakeSidebarHandler(tabs=2)
        self.assertTrue(h.fleet_preparation_sidebar_ensure(3))
        self.assertEqual(h.current, 2)
        self.assertFalse(h.closed)

    def test_missing_tab_not_clicked(self):
        h = FakeSidebarHandler(tabs=1)
        self.assertFalse(h.fleet_preparation_sidebar_ensure(3))
        self.assertFalse(h.closed)
        self.assertEqual(h.device.clicks, [])


if __name__ == '__main__':
    unittest.main()
