"""Tests for giving up destinations that the fleet can't reach (no device interaction)."""
import copy
import unittest
from unittest import mock

from campaign.campaign_main.campaign_8_3 import MAP as MAP_8_3
from module.campaign.campaign_base import CampaignBase
from module.exception import MapEnemyMoved
from module.map.fleet import Fleet
from module.map.map_base import node2location as loc


def map_8_3():
    """
    Real case: 8-3 battle 2, fleet 1 at G5, fleet 2 at E4, submarine at C5.
    Enemies at G4, H4, E6 box fleet 1 in, but H4 was missed in full scan.
    """
    map_ = copy.deepcopy(MAP_8_3)
    map_.reset()
    map_.grid_connection_initial(wall=False, portal=False)
    for grid in map_:
        grid.is_enemy = False
        grid.is_fleet = False
        grid.enemy_scale = 0
    for name in ['A3', 'C1', 'G4', 'E6', 'D6']:
        map_[loc(name)].is_enemy = True
    map_[loc('G5')].is_fleet = True
    map_[loc('E4')].is_fleet = True
    return map_


class FakeConfig:
    MAP_HAS_AMBUSH = False
    MAP_HAS_FORTRESS = False
    MAP_HAS_DECOY_ENEMY = False
    Submarine_Mode = 'do_not_use'
    MAP_HAS_LAND_BASED = False
    Campaign_UseFleetLock = False
    MAP_WALK_USE_CURRENT_FLEET = False


class FakeGrid:
    """Local grid on screen, fleet never shows up on it."""

    def __init__(self, location):
        self.location = location

    def predict_fleet(self):
        return False

    def predict_current_fleet(self):
        return False


class ArrivedGrid(FakeGrid):
    def predict_fleet(self):
        return True


class FakeTimer:
    """All timers reach immediately."""

    def __init__(self, limit, count=0):
        self.limit = limit
        self._started = False

    def start(self):
        self._started = True
        return self

    def started(self):
        return self._started

    def reached(self):
        return True

    def reset(self):
        return self


class FakeDevice:
    image = None

    def __init__(self):
        self.clicks = []

    def click(self, grid):
        self.clicks.append(grid.location)

    def screenshot(self):
        return None


class FakeView:
    def update(self, image=None):
        pass


class FakeFleet(Fleet):
    def __init__(self):
        self.config = FakeConfig()
        self.map = map_8_3()
        self.device = FakeDevice()
        self.view = FakeView()
        self.fleet_1_location = loc('G5')
        self.fleet_2_location = loc('E4')
        self.fleet_submarine_location = loc('C5')
        self.fleet_current_index = 1
        self.walk_unreachable = set()
        self.walk_unreachable_enabled = False
        self.predict_count = 0

    round_is_new = False
    round_maze_changed = False
    round_wait = 0

    def round_next(self):
        pass

    def hp_retreat_triggered(self):
        return False

    def fleet_ensure(self, index):
        pass

    def in_sight(self, location, sight=None):
        pass

    def focus_to_grid_center(self, *args, **kwargs):
        pass

    def convert_global_to_local(self, location):
        return FakeGrid(location)

    def ambush_color_initial(self):
        pass

    def enemy_searching_color_initial(self):
        pass

    def combat_appear(self):
        return False

    def handle_ambush(self):
        return False

    def handle_mystery(self, button=None):
        return False

    def handle_map_cat_attack(self):
        return False

    def handle_guild_popup_cancel(self):
        return False

    def handle_walk_out_of_step(self):
        return False

    def is_in_map(self):
        return True

    def predict(self):
        self.predict_count += 1

    def ensure_edge_insight(self, *args, **kwargs):
        pass


class TestFindPathBlocked(unittest.TestCase):
    def test_blocked_grid(self):
        map_ = map_8_3()
        map_.find_path_initial(loc('G5'), has_ambush=False)
        # H4 missed, Alas thinks A3 is reachable through H4
        self.assertTrue(map_[loc('A3')].is_accessible)
        map_.find_path_initial(loc('G5'), has_ambush=False, blocked=[loc('A3')])
        self.assertFalse(map_[loc('A3')].is_accessible)
        self.assertTrue(map_[loc('G4')].is_accessible)
        self.assertTrue(map_[loc('D6')].is_accessible)

    def test_blocked_per_fleet(self):
        fleet = FakeFleet()
        fleet.walk_unreachable = {(1, loc('A3'))}
        fleet.find_path_initial()
        a3 = fleet.map[loc('A3')]
        self.assertFalse(a3.is_accessible)
        self.assertFalse(a3.is_accessible_1)
        # Only blocked for fleet 1
        self.assertTrue(a3.is_accessible_2)


class WalkTestBase(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch('module.map.fleet.Timer', FakeTimer)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestWalkUnreachable(WalkTestBase):
    def test_giveup_after_retry(self):
        # Fleet never moves, give up after WALK_UNREACHABLE_RETRY timeouts
        fleet = FakeFleet()
        fleet.walk_unreachable_enabled = True
        with self.assertRaises(MapEnemyMoved):
            fleet._goto(loc('A3'), expected='combat')
        self.assertEqual(len(fleet.device.clicks), Fleet.WALK_UNREACHABLE_RETRY)
        self.assertEqual(fleet.walk_unreachable, {(1, loc('A3'))})
        self.assertFalse(fleet.map[loc('A3')].is_accessible)
        self.assertEqual(fleet.fleet_1_location, loc('G5'))
        self.assertEqual(fleet.predict_count, Fleet.WALK_UNREACHABLE_RETRY)

    def test_disabled_outside_battle(self):
        # Outside execute_a_battle (map_init, OS), keep retrying as before
        fleet = FakeFleet()
        fleet.WALK_UNREACHABLE_RETRY = 1

        def predict():
            fleet.predict_count += 1
            if fleet.predict_count >= 5:
                raise RuntimeError('stop')

        fleet.predict = predict
        with self.assertRaises(RuntimeError):
            fleet._goto(loc('A3'), expected='combat')
        self.assertEqual(len(fleet.device.clicks), 5)
        self.assertEqual(fleet.walk_unreachable, set())

    def test_cleared_after_fleet_moved(self):
        fleet = FakeFleet()
        fleet.walk_unreachable = {(1, loc('A3'))}
        fleet.map[loc('G4')].is_enemy = False
        fleet.convert_global_to_local = lambda location: ArrivedGrid(location)
        fleet._goto(loc('G4'))
        self.assertEqual(fleet.walk_unreachable, set())
        self.assertEqual(fleet.fleet_1_location, loc('G4'))
        self.assertTrue(fleet.map[loc('A3')].is_accessible)


class FakeCampaign(CampaignBase):
    FUNCTION_NAME_BASE = 'battle_'

    def __init__(self, raises):
        self.battle_count = 0
        self.raises = raises
        self.enabled = []
        self.walk_unreachable_enabled = False

    def battle_function(self):
        self.enabled.append(self.walk_unreachable_enabled)
        if self.raises:
            self.raises -= 1
            raise MapEnemyMoved
        self.battle_count += 1
        return True


class TestExecuteABattle(unittest.TestCase):
    def test_enabled_in_battle(self):
        campaign = FakeCampaign(raises=2)
        self.assertTrue(campaign.execute_a_battle())
        self.assertEqual(campaign.enabled, [True, True, True])
        self.assertFalse(campaign.walk_unreachable_enabled)


if __name__ == '__main__':
    unittest.main()
