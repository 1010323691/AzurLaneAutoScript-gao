"""Tests for enhancing outside the map every N sorties (no device interaction)."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import module.campaign.run as run_module
from module.campaign.run import CampaignRun


class FakeDevice:
    has_cached_image = True
    image = None

    def screenshot(self):
        pass

    def click_record_clear(self):
        pass


class FakeCampaign:
    def __init__(self, in_map=False, in_auto_search_menu=False, total=10):
        self.device = SimpleNamespace(image=None)
        self.in_map = in_map
        self.in_auto_search_menu = in_auto_search_menu
        self.total = total
        self.calls = []
        self._unable_to_enhance = False

    def is_in_map(self):
        return self.in_map

    def is_in_auto_search_menu(self):
        return self.in_auto_search_menu

    def ensure_auto_search_exit(self):
        self.calls.append('auto_search_exit')

    def ui_ensure(self, page):
        self.calls.append(('ui_ensure', page))

    def handle_dock_cards_loading(self):
        pass

    def enhance_ships(self):
        self.calls.append('enhance')
        return self.total


def make_run(mode='enhance', interval=5, run_count=5, campaign=None):
    run = CampaignRun.__new__(CampaignRun)
    run.config = SimpleNamespace(Retirement_RetireMode=mode, Enhance_SortieInterval=interval)
    run.device = FakeDevice()
    run.campaign = campaign or FakeCampaign()
    run.run_count = run_count
    return run


def fake_ocr(remain):
    return patch.object(run_module.OCR_DOCK_AMOUNT, 'ocr', lambda image: (0, remain, 0))


class TestTriggered(unittest.TestCase):
    def test_every_interval(self):
        triggered = [n for n in range(0, 16) if make_run(run_count=n).enhance_between_sorties_triggered()]
        self.assertEqual(triggered, [5, 10, 15])

    def test_disabled(self):
        self.assertFalse(make_run(interval=0).enhance_between_sorties_triggered())
        self.assertFalse(make_run(mode='one_click_retire').enhance_between_sorties_triggered())
        self.assertFalse(make_run(mode='old_retire').enhance_between_sorties_triggered())


class TestEnhance(unittest.TestCase):
    def test_enhance_in_dock(self):
        run = make_run()
        with fake_ocr(100):
            self.assertTrue(run.enhance_between_sorties())
        self.assertEqual(run.campaign.calls, [('ui_ensure', run_module.page_dock), 'enhance'])
        self.assertFalse(run.campaign._unable_to_enhance)

    def test_exit_auto_search_menu(self):
        run = make_run(campaign=FakeCampaign(in_auto_search_menu=True))
        with fake_ocr(100):
            self.assertTrue(run.enhance_between_sorties())
        self.assertEqual(run.campaign.calls[0], 'auto_search_exit')
        self.assertIn('enhance', run.campaign.calls)

    def test_skip_in_map(self):
        run = make_run(campaign=FakeCampaign(in_map=True))
        self.assertFalse(run.enhance_between_sorties())
        self.assertEqual(run.campaign.calls, [])

    def test_retire_in_map_if_unable(self):
        # Nothing enhanced, or few spare docks: retire directly when dock is full in map
        run = make_run(campaign=FakeCampaign(total=0))
        with fake_ocr(100):
            run.enhance_between_sorties()
        self.assertTrue(run.campaign._unable_to_enhance)

        run = make_run()
        with fake_ocr(2):
            run.enhance_between_sorties()
        self.assertTrue(run.campaign._unable_to_enhance)


if __name__ == '__main__':
    unittest.main()
