"""Tests for focusing camera on the current fleet by switching fleets twice (no device interaction)."""
import unittest

from module.map.fleet import Fleet


class FakeConfig:
    def __init__(self, fleet_2=2):
        self.FLEET_2 = fleet_2


class FakeView:
    def __init__(self, name):
        self.name = name


class FakeFleet(Fleet):
    def __init__(self, fleet_2=2, current=1, switch_ok=True, back_ok=True, camera=(6, 2)):
        self.config = FakeConfig(fleet_2)
        self.fleet_current_index = current
        self.switch_ok = switch_ok
        self.back_ok = back_ok
        self.switched_away = False
        self.calls = []
        self.camera = camera
        self.view = FakeView('before switch')
        self._prev_view = None
        self._prev_swipe = None

    def fleet_set(self, index=None, skip_first_screenshot=True):
        self.calls.append(('fleet_set', index))
        if index == self.fleet_current_index:
            return False
        if not self.switch_ok:
            return False
        if self.switched_away and not self.back_ok:
            # Switching back fails, game stays on the other fleet
            return False
        self.switched_away = not self.switched_away
        self.fleet_current_index = index
        return True

    def update(self, camera=True, wait_swipe=False, allow_error=False):
        self.calls.append(('update', self._prev_swipe))


class TestFleetFocusBySwitch(unittest.TestCase):
    def test_switch_twice_and_focus(self):
        # Real case: 4-4, camera G3, fleet arrived D3 below submarine at D2, arrow hidden under the top UI
        fleet = FakeFleet(current=1, camera=(6, 2))
        self.assertTrue(fleet.fleet_focus_by_switch((3, 2)))
        # Camera moves like a map swipe, so swipe prediction can correct it if the fleet is elsewhere
        self.assertEqual(fleet.calls, [('fleet_set', 2), ('fleet_set', 1), ('update', (-3, 0))])
        self.assertEqual(fleet._prev_view.name, 'before switch')
        self.assertEqual(fleet.fleet_current_index, 1)

    def test_fleet_2_current(self):
        fleet = FakeFleet(current=2)
        self.assertTrue(fleet.fleet_focus_by_switch((3, 2)))
        self.assertEqual(fleet.calls[:2], [('fleet_set', 1), ('fleet_set', 2)])
        self.assertEqual(fleet.fleet_current_index, 2)

    def test_location_is_camera(self):
        fleet = FakeFleet(camera=(3, 2))
        self.assertTrue(fleet.fleet_focus_by_switch((3, 2)))
        self.assertIsNone(fleet._prev_view)
        self.assertEqual(fleet.calls[-1], ('update', None))

    def test_single_fleet(self):
        fleet = FakeFleet(fleet_2=0)
        self.assertFalse(fleet.fleet_focus_by_switch((3, 2)))
        self.assertEqual(fleet.calls, [])
        self.assertIsNone(fleet._prev_view)

    def test_switch_failed(self):
        # SWITCH_OVER not found, camera unchanged, walk falls back to the original retry
        fleet = FakeFleet(switch_ok=False)
        self.assertFalse(fleet.fleet_focus_by_switch((3, 2)))
        self.assertEqual(fleet.calls, [('fleet_set', 2)])
        self.assertIsNone(fleet._prev_view)

    def test_switch_back_failed(self):
        # Stays on the other fleet, retry once, then give up without moving camera
        fleet = FakeFleet(current=1, back_ok=False)
        self.assertFalse(fleet.fleet_focus_by_switch((3, 2)))
        self.assertEqual(fleet.calls, [('fleet_set', 2), ('fleet_set', 1), ('fleet_set', 1)])
        self.assertEqual(fleet.fleet_current_index, 2)
        self.assertIsNone(fleet._prev_view)

    def test_switch_back_retry(self):
        # First switch back fails, retry succeeds
        fleet = FakeFleet(current=1, back_ok=False)
        original = fleet.fleet_set

        def fleet_set(index=None, skip_first_screenshot=True):
            if len(fleet.calls) == 2:
                fleet.back_ok = True
            return original(index=index)

        fleet.fleet_set = fleet_set
        self.assertTrue(fleet.fleet_focus_by_switch((3, 2)))
        self.assertEqual(fleet.fleet_current_index, 1)


if __name__ == '__main__':
    unittest.main()
