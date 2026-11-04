"""Tests for zones pinned as main story chapter "メイン海域" (no device interaction)."""
import os
import unittest
from unittest import mock

import numpy as np
from PIL import Image

# Import campaign modules first: importing module.os alone fails on adbutils' pkg_resources in .venv
from module.campaign.campaign_base import CampaignBase  # noqa: F401
import module.config.server as server
from module.base.resource import release_resources
from module.os.assets import SELECT_DANGEROUS, SELECT_SAFE, ZONE_MAIN
from module.os.globe_operation import GlobeOperation

HERE = os.path.dirname(__file__)
# JP screenshots, area (0, 140, 560, 500) kept, the rest is black.
# Zone 32 pinned right after clicking it on globe: "Chapter5-1 泥沼" + "メイン海域"
MAIN = os.path.join(HERE, 'zone_pinned_main_jp.png')
# Same zone after choosing "探索海域" in ZONE_SWITCH: "南西海嶺B" + "探索海域"
DANGEROUS = os.path.join(HERE, 'zone_pinned_dangerous_jp.png')
# ZONE_SWITCH expanded: メイン海域 / 秘密座標 / 確保海域 / 探索海域
SELECT = os.path.join(HERE, 'zone_select_main_jp.png')


class FakeDevice:
    image = None


class FakeGlobe(GlobeOperation):
    def __init__(self, file):
        self.device = FakeDevice()
        self.device.image = np.array(Image.open(file).convert('RGB'))

    def appear(self, button, offset=0, interval=0, similarity=0.85, threshold=10):
        return button.match(self.device.image, offset=offset, similarity=similarity)


class TestZoneMain(unittest.TestCase):
    def setUp(self):
        self.server = server.server
        server.server = 'jp'
        release_resources()
        self.addCleanup(self.restore_server)

    def restore_server(self):
        server.server = self.server
        release_resources()

    def test_main_zone_pinned(self):
        globe = FakeGlobe(MAIN)
        self.assertTrue(globe.is_zone_pinned())
        self.assertEqual(globe.get_zone_pinned_name(), 'MAIN')

    def test_dangerous_not_main(self):
        globe = FakeGlobe(DANGEROUS)
        self.assertEqual(globe.get_zone_pinned_name(), 'DANGEROUS')

    def test_select_expanded_not_pinned(self):
        """zone_select_execute() ends on is_zone_pinned(), it must be False while selection is expanded."""
        globe = FakeGlobe(SELECT)
        self.assertFalse(globe.is_zone_pinned())
        self.assertIn(SELECT_DANGEROUS, globe.get_zone_select())
        self.assertIn(SELECT_SAFE, globe.get_zone_select())

    def test_type_select_from_main(self):
        """Main zone isn't in ('SAFE', 'DANGEROUS'), so choose one through ZONE_SWITCH."""
        globe = FakeGlobe(MAIN)
        globe.zone_has_switch = mock.MagicMock(return_value=True)
        globe.zone_select_enter = mock.MagicMock()
        globe.ensure_zone_select_expanded = mock.MagicMock(return_value=[SELECT_DANGEROUS])
        globe.zone_select_execute = mock.MagicMock()
        globe.get_zone_pinned_name = mock.MagicMock(side_effect=['MAIN', 'DANGEROUS'])
        self.assertTrue(globe.zone_type_select(types=('SAFE', 'DANGEROUS')))
        globe.zone_select_execute.assert_called_once_with(SELECT_DANGEROUS)

    def test_main_asset_from_cn(self):
        self.assertEqual(ZONE_MAIN.name, 'ZONE_MAIN')


if __name__ == '__main__':
    unittest.main()
