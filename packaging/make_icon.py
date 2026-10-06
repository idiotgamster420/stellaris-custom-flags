"""Draws the app icon (app/icon.png and app/icon.ico for Windows): a hexagonal flag with a split field and a star.
Original artwork, so it can ship with the app (no game files)."""
import math
from pathlib import Path

import cairo

SIZE = 256


def hexagon(cr, cx, cy, r):
    for i in range(6):
        a = math.radians(60 * i - 90)
        (cr.move_to if i == 0 else cr.line_to)(cx + r * math.cos(a), cy + r * math.sin(a))
    cr.close_path()


def star(cr, cx, cy, outer, inner, points=5):
    for i in range(points * 2):
        r = outer if i % 2 == 0 else inner
        a = math.radians(-90 + i * 180 / points)
        (cr.move_to if i == 0 else cr.line_to)(cx + r * math.cos(a), cy + r * math.sin(a))
    cr.close_path()


surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, SIZE, SIZE)
cr = cairo.Context(surface)
c, r = SIZE / 2, SIZE * 0.47

hexagon(cr, c, c, r)  # metal frame
frame = cairo.LinearGradient(0, 0, 0, SIZE)
frame.add_color_stop_rgb(0, 0.42, 0.62, 0.55)
frame.add_color_stop_rgb(1, 0.20, 0.34, 0.30)
cr.set_source(frame)
cr.fill()

hexagon(cr, c, c, r * 0.86)  # the flag: a diagonal split, like a flag background
cr.save()
cr.clip()
cr.set_source_rgb(0.11, 0.24, 0.55)
cr.paint()
cr.move_to(0, SIZE)
cr.line_to(SIZE, 0)
cr.line_to(SIZE, SIZE)
cr.close_path()
cr.set_source_rgb(0.93, 0.55, 0.13)
cr.fill()
gloss = cairo.LinearGradient(0, 0, 0, SIZE)  # the same top-to-bottom gloss as flags in game
gloss.add_color_stop_rgba(0, 1, 1, 1, 0.28)
gloss.add_color_stop_rgba(0.55, 1, 1, 1, 0.0)
gloss.add_color_stop_rgba(1, 0, 0, 0, 0.25)
cr.set_source(gloss)
cr.paint()
cr.restore()

star(cr, c, c * 1.02, r * 0.5, r * 0.21)
cr.set_source_rgb(0.98, 0.97, 0.92)
cr.fill_preserve()
cr.set_source_rgba(0, 0, 0, 0.35)
cr.set_line_width(3)
cr.stroke()

out = Path(__file__).resolve().parent.parent / "app" / "icon.png"
surface.write_to_png(str(out))
from PIL import Image  # the Windows icon, with the sizes Windows asks for
Image.open(out).save(out.with_suffix(".ico"), sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("wrote", out, "and", out.with_suffix(".ico"))
