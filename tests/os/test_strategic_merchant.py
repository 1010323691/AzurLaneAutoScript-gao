# -*- coding: utf-8 -*-
"""Tests for strategic search merchant options pushed below the visible list on JP (no device interaction)."""
import os
import unittest
from unittest import mock

import numpy as np
from PIL import Image

# Import campaign modules first: importing module.os alone fails on adbutils' pkg_resources in .venv
from module.campaign.campaign_base import CampaignBase  # noqa: F401
import module.config.server as server
from module.base.base import ModuleBase
from module.base.resource import release_resources
from module.os_handler import strategic
from module.os_handler.assets import STRATEGIC_SEARCH_MERCHANT_CHECK, STRATEGIC_SEARCH_MERCHANT_STOP
from module.os_handler.strategic import StrategicSearchHandler

HERE = os.path.dirname(__file__)
# JP, 2026-11-16: popup (350, 150, 930, 575) kept, the rest is black.
# Zone mode "同じエリアに進入" wraps into two lines, merchant title "謎の商人出現時" at y=433~454,
# merchant options clipped by the list bottom (y=468).
HIDDEN = os.path.join(HERE, 'strategic_merchant_hidden_jp.png')
# Zone mode tick in HIDDEN
TICK = (581, 349, 601, 363)
LIST_TOP, LIST_BOTTOM = 277, 468


def load(file):
    return np.array(Image.open(file).convert('RGB'))


def scrolled(image, shift, tick_x=None):
    """
    Simulate list scrolled down by `shift` pixels, with merchant tick drawn at tick_x.
    """
    out = image.copy()
    out[LIST_TOP:LIST_BOTTOM - shift, 380:900] = image[LIST_TOP + shift:LIST_BOTTOM, 380:900]
    out[LIST_BOTTOM - shift:LIST_BOTTOM, 380:900] = image[440:441, 380:900]
    if tick_x is not None:
        patch = image[TICK[1]:TICK[3], TICK[0]:TICK[2]]
        # Merchant title bottom is 454 - shift, tick about 35px below
        y = 454 - shift + 30
        out[y:y + patch.shape[0], tick_x:tick_x + patch.shape[1]] = patch
    return out


class FakeDevice:
    def __init__(self, image):
        self.image = image
        self.clicks = []

    def screenshot(self):
        return self.image

    def click(self, button, control_check=True):
        self.clicks.append(button)

    def stuck_record_clear(self):
        pass

    def click_record_clear(self):
        pass


class FakeHandler(StrategicSearchHandler):
    def __init__(self, image):
        self.device = FakeDevice(image)
        self.interval_timer = {}

    def loop(self, skip_first=True, timeout=None):
        for _ in range(30):
            yield self.device.image

    def appear(self, button, offset=0, interval=0, similarity=0.85, threshold=10):
        return button.match(self.device.image, offset=offset, similarity=similarity)

    def image_crop(self, button, copy=True):
        return ModuleBase.image_crop(self, button, copy=copy)


class TestStrategicMerchant(unittest.TestCase):
    def setUp(self):
        self.server = server.server
        server.server = 'jp'
        release_resources()
        self.addCleanup(self.restore_server)
        self.hidden = load(HIDDEN)

    def restore_server(self):
        server.server = self.server
        release_resources()

    def test_title_found(self):
        handler = FakeHandler(self.hidden)
        self.assertTrue(handler.appear(STRATEGIC_SEARCH_MERCHANT_CHECK, offset=(20, 200), similarity=0.75))

    def test_hidden_options(self):
        """Merchant options clipped at the bottom, neither default position shows a tick."""
        handler = FakeHandler(self.hidden)
        self.assertIsNone(handler._strategy_merchant_tick())
        self.assertFalse(handler._strategy_option_selected(STRATEGIC_SEARCH_MERCHANT_STOP))

    def test_tick_after_scroll(self):
        handler = FakeHandler(scrolled(self.hidden, 60, tick_x=570))
        x, y = handler._strategy_merchant_tick()
        self.assertLess(abs(x - 580), 5)
        self.assertLess(abs(y - (454 - 60 + 37)), 5)

    def test_zone_tick_not_counted(self):
        """Scrolled without merchant tick: zone mode tick above the title must not be picked up."""
        handler = FakeHandler(scrolled(self.hidden, 60))
        self.assertIsNone(handler._strategy_merchant_tick())

    def run_set_merchant(self, tick_x):
        handler = FakeHandler(self.hidden)
        after = scrolled(self.hidden, 60, tick_x=tick_x)

        def scroll_set(position, main, **kwargs):
            handler.device.image = after
            return True

        with mock.patch.object(strategic.STRATEGIC_SEARCH_SCROLL, 'cal_position', return_value=0.), \
                mock.patch.object(strategic.STRATEGIC_SEARCH_SCROLL, 'set', side_effect=scroll_set) as set_:
            result = handler._strategic_search_set_merchant()
        return handler, result, set_

    def test_set_merchant_already_stop(self):
        handler, result, set_ = self.run_set_merchant(tick_x=570)
        self.assertTrue(result)
        set_.assert_called_once()
        self.assertEqual(handler.device.clicks, [])

    def test_set_merchant_continue(self):
        """Tick in the right column: click stop on the same row; gives up after 3 clicks without change."""
        handler, result, _ = self.run_set_merchant(tick_x=768)
        self.assertGreaterEqual(len(handler.device.clicks), 1)
        click = handler.device.clicks[0]
        self.assertEqual(click.button[0], STRATEGIC_SEARCH_MERCHANT_STOP.button[0])
        center_y = (click.button[1] + click.button[3]) / 2
        self.assertLess(abs(center_y - (454 - 60 + 37)), 6)

    def test_never_found(self):
        handler = FakeHandler(self.hidden)
        with mock.patch.object(strategic.STRATEGIC_SEARCH_SCROLL, 'cal_position', return_value=0.), \
                mock.patch.object(strategic.STRATEGIC_SEARCH_SCROLL, 'set', return_value=True) as set_:
            self.assertFalse(handler._strategic_search_set_merchant())
        self.assertEqual(set_.call_count, 4)

    def test_set_option_falls_back(self):
        """strategic_search_set_option() no longer waits forever on the JP layout."""
        handler = FakeHandler(self.hidden)
        handler._strategic_search_set_merchant = mock.MagicMock(return_value=False)
        with mock.patch.object(strategic, 'Timer') as timer:
            timer.return_value.reached.return_value = True
            self.assertFalse(handler.strategic_search_set_option())
        handler._strategic_search_set_merchant.assert_called_once()


if __name__ == '__main__':
    unittest.main()
