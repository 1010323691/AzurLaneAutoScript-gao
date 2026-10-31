"""Tests for focusing camera on the current fleet by switching fleets twice (no device interaction)."""
import unittest

from module.map.fleet import Fleet


class FakeConfig:
    def __init__(self, fleet_2=2):
        self.FLEET_2 = fleet_2


class FakeFleet(Fleet):
    def __init__(self, fleet_2=2, current=1, switch_ok=True):
        self.config = FakeConfig(fleet_2)
        self.fleet_current_index = current
        self.switch_ok = switch_ok
        self.calls = []
        self.camera = (0, 0)

    def fleet_set(self, index=None, skip_first_screenshot=True):
        self.calls.append(('fleet_set', index))
        if not self.switch_ok:
            return False
        switched = index != self.fleet_current_index
        self.fleet_current_index = index
        return switched

    def update(self, camera=True, wait_swipe=False, allow_error=False):
        self.calls.append(('update', self.camera))


class TestFleetFocusBySwitch(unittest.TestCase):
    def test_switch_twice_and_focus(self):
        # Real case: 4-4, fleet arrived D3 below submarine at D2, arrow hidden under the top UI
        fleet = FakeFleet(current=1)
        self.assertTrue(fleet.fleet_focus_by_switch((3, 2)))
        self.assertEqual(fleet.calls, [('fleet_set', 2), ('fleet_set', 1), ('update', (3, 2))])
        self.assertEqual(fleet.fleet_current_index, 1)
        self.assertEqual(fleet.camera, (3, 2))

    def test_fleet_2_current(self):
        fleet = FakeFleet(current=2)
        self.assertTrue(fleet.fleet_focus_by_switch((3, 2)))
        self.assertEqual(fleet.calls[:2], [('fleet_set', 1), ('fleet_set', 2)])

    def test_single_fleet(self):
        fleet = FakeFleet(fleet_2=0)
        self.assertFalse(fleet.fleet_focus_by_switch((3, 2)))
        self.assertEqual(fleet.calls, [])
        self.assertEqual(fleet.camera, (0, 0))

    def test_switch_failed(self):
        # SWITCH_OVER not found, camera unchanged, walk falls back to the original retry
        fleet = FakeFleet(switch_ok=False)
        self.assertFalse(fleet.fleet_focus_by_switch((3, 2)))
        self.assertEqual(fleet.camera, (0, 0))


if __name__ == '__main__':
    unittest.main()
