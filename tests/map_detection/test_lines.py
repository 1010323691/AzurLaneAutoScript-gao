"""Tests for line crossings in map detection (no device interaction)."""
import unittest

import numpy as np

from module.map_detection.utils import Lines


class TestLinesCross(unittest.TestCase):
    def test_cross(self):
        # rho, theta. Horizontal lines at y=100 and y=300, vertical lines at x=200 and x=600
        horizontal = Lines(np.array([[100, np.pi / 2], [300, np.pi / 2]]), is_horizontal=True)
        vertical = Lines(np.array([[200, 0], [600, 0]]), is_horizontal=False)
        points = horizontal.cross(vertical).points
        self.assertEqual(points.shape, (4, 2))
        expected = [[200, 100], [600, 100], [200, 300], [600, 300]]
        self.assertTrue(np.allclose(points, expected))


if __name__ == '__main__':
    unittest.main()
