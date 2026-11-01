"""圆环方向、未知状态和边缘覆盖的像素级信号。"""
import unittest
from cpu_memory_cleanup.interaction.ring import RingRaster


class RingTests(unittest.TestCase):
    def test_clockwise_quarter_changes_only_the_top_right_quadrant(self):
        ring = RingRaster(80, 10, 8, "#ffffff", "#dddddd", "#008800")
        pixels = ring.render(25)[len(ring.header):]
        def pixel(x, y):
            offset = (y*80+x)*3
            return tuple(pixels[offset:offset+3])
        self.assertEqual(pixel(61, 18), (0, 136, 0))
        self.assertEqual(pixel(61, 61), (221, 221, 221))
        self.assertEqual(pixel(18, 18), (221, 221, 221))
        self.assertEqual(pixel(40, 40), (255, 255, 255))

    def test_inner_and_outer_edges_have_partial_pixel_coverage(self):
        ring = RingRaster(80, 10, 8, "#ffffff", "#dddddd", "#008800")
        pixels = ring.render(100)[len(ring.header):]
        for lower, upper in ((25, 27), (33, 35)):
            colors = [tuple(pixels[offset:offset+3]) for offset, _, distance, _ in ring.pixels
                      if lower < distance < upper]
            self.assertTrue(any(0 < r < 255 and 136 < g < 255 for r, g, _ in colors))

    def test_unknown_matches_empty_track_and_full_circle_has_no_seam(self):
        ring = RingRaster(80, 10, 8, "#ffffff", "#dddddd", "#008800")
        self.assertEqual(ring.render(None), ring.render(0))
        pixels = ring.render(100)[len(ring.header):]
        for x, y in ((39, 10), (40, 10)):
            offset = (y*80+x)*3
            self.assertEqual(tuple(pixels[offset:offset+3]), (0, 136, 0))
