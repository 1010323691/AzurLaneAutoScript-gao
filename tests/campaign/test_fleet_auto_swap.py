"""Tests for Fleet.FleetOrder=fleet1_mob_fleet2_boss_auto_swap (no device interaction)."""
import json
import os
import unittest
from datetime import datetime
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
