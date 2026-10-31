"""Tests for giving up submarine move when clicks have no response (no device interaction)."""
import unittest
from unittest.mock import patch

import module.map.fleet as fleet_module
from module.map.fleet import Fleet


class FakeTimer:
    """Walk timeout reaches at once, so tests don't wait 2s per retry."""

    def __init__(self, limit, count=0):
        self._started = False

    def start(self):
        self._started = True
        return self

    def started(self):
        return self._started

    def reached(self):
        return self._started


class FakeGrid:
    def __init__(self, arrive_after=None):
        # Number of clicks before submarine arrives, None for never
        self.arrive_after = arrive_after
        self.clicks = 0

    def predict_submarine_move(self):
        return self.arrive_after is not None and self.clicks >= self.arrive_after

    def predict_submarine(self):
        return False

    def predict_fleet(self):
        return False


class FakeDevice:
    image = None

    def __init__(self, grid):
        self.grid = grid

    def click(self, button):
        self.grid.clicks += 1

    def screenshot(self):
        pass


class FakeView:
    def update(self, image):
        pass


class FakeMap:
    camera_sight = (-3, -1, 3, 2)


class FakeFleet(Fleet):
    def __init__(self, grid):
        self.map = FakeMap()
        self.grid = grid
        self.device = FakeDevice(grid)
        self.view = FakeView()
        self.calls = []

    def in_sight(self, location, sight=None):
        pass

    def focus_to_grid_center(self, tolerance=None):
        pass

    def convert_global_to_local(self, location):
        return self.grid

    def predict(self):
        pass

    def ensure_edge_insight(self, reverse=False, preset=None, swipe_limit=(3, 2), skip_first_update=True):
        self.calls.append('ensure_edge_insight')


@patch.object(fleet_module, 'Timer', FakeTimer)
class TestSubmarineMoveGiveUp(unittest.TestCase):
    def test_no_response_give_up(self):
        # Real case: 5-4, submarine at G3 with 0 ammo, clicks on G2 had no response until GameTooManyClickError
        grid = FakeGrid(arrive_after=None)
        fleet = FakeFleet(grid)
        self.assertFalse(fleet._submarine_goto((6, 1)))
        self.assertEqual(grid.clicks, Fleet.SUBMARINE_MOVE_RETRY)
        # Less than the 12 clicks of GameTooManyClickError
        self.assertLess(grid.clicks, 12)

    def test_arrive_after_retry(self):
        grid = FakeGrid(arrive_after=2)
        fleet = FakeFleet(grid)
        self.assertTrue(fleet._submarine_goto((6, 1)))
        self.assertEqual(grid.clicks, 2)

    def test_arrive_at_once(self):
        grid = FakeGrid(arrive_after=1)
        fleet = FakeFleet(grid)
        self.assertTrue(fleet._submarine_goto((6, 1)))
        self.assertEqual(grid.clicks, 1)
        self.assertEqual(fleet.calls, [])


if __name__ == '__main__':
    unittest.main()
