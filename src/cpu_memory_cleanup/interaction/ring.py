"""圆环的像素覆盖渲染；仅生成Tk可读图像，不接入系统或第三方图形库。"""
import math


class RingRaster:
    def __init__(self, size, inset, stroke, background, track, fill):
        self.size = size
        colors = [tuple(bytes.fromhex(color.lstrip("#"))) for color in (background, track, fill)]
        self.background, self.track, self.fill = colors
        self.base = bytes(self.background) * (size * size)
        self.header = f"P6\n{size} {size}\n255\n".encode("ascii")
        radius = size / 2 - inset
        self.pixels = []
        # 圆周两侧约一个像素宽的覆盖过渡，平滑内外缘；几何仅在尺寸改变时计算。
        for y in range(size):
            dy = y + .5 - size / 2
            for x in range(size):
                dx = x + .5 - size / 2
                distance = math.hypot(dx, dy)
                coverage = min(1., max(0., stroke / 2 + .5 - abs(distance - radius)))
                if coverage:
                    angle = math.atan2(dx, -dy) % math.tau
                    self.pixels.append(((y * size + x) * 3, angle, distance, coverage))

    def render(self, percent):
        extent = min(100., max(0., percent or 0.)) / 100 * math.tau
        image = bytearray(self.base)
        bg, track, fill = self.background, self.track, self.fill
        for offset, angle, distance, coverage in self.pixels:
            weight = (1. if extent == math.tau else 0. if extent == 0 else
                      min(1., max(0., .5 + (extent - angle) * distance)))
            image[offset:offset+3] = bytes(round(b + (t + (f-t)*weight - b)*coverage)
                                         for b, t, f in zip(bg, track, fill))
        return self.header + image
