"""Tests for switching to OpsiFleet.Fleet when OpSi resumes a zone (no device interaction)."""
import unittest
from unittest import mock

# Import campaign modules first: importing module.os.map alone fails on adbutils' pkg_resources in .venv
from module.campaign.campaign_base import CampaignBase  # noqa: F401
from module.os.map import OSMap


class FakeZone:
    def __init__(self, zone_id=101, is_port=False):
        self.zone_id = zone_id
        self.is_port = is_port


class FakeConfig:
    def __init__(self, bound=True, fleet=1):
        self.bound = {'OpsiFleet_Fleet': 'OpsiExplore.OpsiFleet.Fleet'} if bound else {}
        self.OpsiFleet_Fleet = fleet


def make_map(bound=True, fleet=1, is_port=False):
    m = OSMap.__new__(OSMap)
    m.__dict__['config'] = FakeConfig(bound=bound, fleet=fleet)
    m.zone = FakeZone(is_port=is_port)
    m.fleet_set = mock.MagicMock(return_value=True)
    return m


class TestFleetSetConfigured(unittest.TestCase):
    def test_switch_to_configured_fleet(self):
        m = make_map(fleet=1)
        self.assertTrue(m.fleet_set_configured())
        m.fleet_set.assert_called_once_with(1)

    def test_task_without_opsi_fleet(self):
        m = make_map(bound=False)
        self.assertFalse(m.fleet_set_configured())
        m.fleet_set.assert_not_called()

    def test_skip_in_port(self):
        m = make_map(is_port=True)
        self.assertFalse(m.fleet_set_configured())
        m.fleet_set.assert_not_called()


class TestOsInitResume(unittest.TestCase):
    def test_set_fleet_before_clearing_current_zone(self):
        """Real case: OpsiExplore resumed in zone 101 with another fleet selected."""
        m = make_map(fleet=1)
        order = []
        m.fleet_set.side_effect = lambda index: order.append(('fleet_set', index))
        m.config.task = mock.MagicMock()
        m.config.task.command = 'OpsiExplore'
        m.config.override = mock.MagicMock()
        m.is_in_map = mock.MagicMock(return_value=True)
        m.zone_init = mock.MagicMock()
        m.hp_reset = mock.MagicMock()
        m.handle_after_auto_search = mock.MagicMock(return_value=False)
        m.handle_current_fleet_resolve = mock.MagicMock()
        m.is_in_special_zone = mock.MagicMock(return_value=False)
        m.run_auto_search = mock.MagicMock(side_effect=lambda **kw: order.append(('run_auto_search',)))
        m.os_init()
        self.assertEqual(order, [('fleet_set', 1), ('run_auto_search',)])


class TestRepairRetry(unittest.TestCase):
    def test_set_fleet_after_repair_and_return(self):
        """Real case: fleet wiped in zone 101, repaired in NY, then back to 101 with the next alive fleet selected."""
        m = make_map(fleet=1)
        order = []
        m.fleet_set.side_effect = lambda index: order.append(('fleet_set', index))
        m.config.task = mock.MagicMock()
        m.config.task.command = 'OpsiExplore'
        m.config.DropRecord_OpsiRecord = 'do_not'
        m.config.OpsiGeneral_DoRandomMapEvent = False
        m.__dict__['stat'] = mock.MagicMock()
        m.device = mock.MagicMock()
        m.handle_ash_beacon_attack = mock.MagicMock()
        m.os_auto_search_run = mock.MagicMock(side_effect=lambda drop: order.append(('auto_search',)) or 1)
        m.hp_reset = mock.MagicMock()
        m.hp_get = mock.MagicMock()
        m.handle_after_auto_search = mock.MagicMock(side_effect=[True, False])
        m.globe_goto = mock.MagicMock(side_effect=lambda zone, types: order.append(('globe_goto', zone.zone_id)))
        m.clear_question = mock.MagicMock()
        with mock.patch.object(OSMap, 'is_in_task_explore', new_callable=mock.PropertyMock, return_value=True):
            self.assertEqual(m.run_auto_search(), 2)
        self.assertEqual(order, [
            ('auto_search',), ('globe_goto', 101), ('fleet_set', 1), ('auto_search',),
        ])


if __name__ == '__main__':
    unittest.main()
