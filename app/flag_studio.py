"""Designer and Maker tabs for the Custom Flags app (flag_uploader.py).

Designer: put a flag together the way the game's flag editor draws it, from the
game's backgrounds, colours and emblems plus your own, to plan an empire's flag.

Maker: a layer editor in the style of Black Ops 2's emblem editor. In Background
mode each layer is painted Primary, Secondary or Black; the game fills those in
with an empire's flag colours (red channel = primary, green = secondary, see
gfx/FX/flag_sprite.shader). Saved backgrounds go to flags/backgrounds/. In Emblem
mode layers have their own colours and save as emblems in the Custom category.
"""
import json
import math
import re
from functools import lru_cache
from pathlib import Path

import cairo
from gi.repository import Gdk, GLib, Gtk, Pango
from PIL import Image, ImageChops

FLAG_HEX = (22, 9, 190, 202)  # bounding box of its hexagon mask
DESIGN_PREVIEW = 300
MAKER_CANVAS = 300
MAKER_PREVIEW = 120
THUMB = 40
MAX_LAYERS = 32  # same as Black Ops 2
SLOTS = {"primary": "Primary", "secondary": "Secondary", "black": "Black"}
CHANNELS = {"primary": (255, 0, 0), "secondary": (0, 255, 0), "black": (0, 0, 0)}
CYAN = (31 / 255, 224 / 255, 202 / 255)
ORANGE, GREY, RED = "#fbaa29", "#c6c6c6", "#fc5646"

CSS = """
.sw-window label.sw-section { font-family: "Malgun Gothic", sans-serif; font-weight: normal; font-size: 15px; color: #fbaa29; }
.sw-window button.sw-small { min-width: 0; min-height: 10px; padding: 0 2px; border-width: 8px 10px; border-image-width: 8px 10px; }
.sw-window popover { background-color: #151d1a; border: 1px solid rgba(31, 224, 202, 0.5); border-radius: 0; box-shadow: 0 0 8px rgba(0, 0, 0, 0.8); padding: 8px; }
.sw-window combobox button { background-image: none; background-color: rgba(0, 0, 0, 0.35); border: 1px solid rgba(31, 224, 202, 0.35); border-radius: 0; box-shadow: none; padding: 2px 8px; }
.sw-window scrolledwindow undershoot.top, .sw-window scrolledwindow undershoot.bottom { background-image: none; }
.sw-window list, .sw-window row { background: transparent; }
.sw-window row { padding: 2px 4px; border: 1px solid transparent; }
.sw-window row:hover { background-color: rgba(31, 224, 202, 0.08); }
.sw-window row:selected { background-color: rgba(31, 224, 202, 0.16); border-color: rgba(31, 224, 202, 0.6); }
.sw-window scale trough { min-height: 6px; border: 1px solid rgba(31, 224, 202, 0.45); border-radius: 0; background-color: rgba(0, 0, 0, 0.35); }
.sw-window scale highlight { background-image: none; background-color: rgba(31, 224, 202, 0.45); border: none; border-radius: 0; }
.sw-window scale slider {
  min-width: 8px; min-height: 18px; margin: -7px 0; border-radius: 0; background-image: none;
  background-color: #e9fffb; border: 1px solid #1fe0ca; box-shadow: 0 0 4px rgba(31, 224, 202, 0.8);
}
"""


# --- shapes: paths in a unit square centred on 0, 0 (filled even-odd, so rings have holes)

def _polygon(cr, points):
    cr.move_to(*points[0])
    for point in points[1:]:
        cr.line_to(*point)
    cr.close_path()


def _regular(cr, sides, r=0.5, start=-90, inner=None):
    n = sides * 2 if inner else sides
    radius = [inner if inner and i % 2 else r for i in range(n)]
    _polygon(cr, [(radius[i] * math.cos(math.radians(start + i * 360 / n)),
                   radius[i] * math.sin(math.radians(start + i * 360 / n))) for i in range(n)])


def _cross(cr, t=0.16):
    _polygon(cr, [(-t, -.5), (t, -.5), (t, -t), (.5, -t), (.5, t), (t, t), (t, .5), (-t, .5), (-t, t), (-.5, t), (-.5, -t), (-t, -t)])


def _saltire(cr):
    cr.save()
    cr.rotate(math.pi / 4)
    cr.scale(math.sqrt(2), math.sqrt(2))
    _cross(cr, 0.1)
    cr.restore()


def _crescent(cr, offset=0.22, inner=0.42):
    # The outer circle minus a circle shifted right; the two meet at (x, +-y).
    x = (0.25 - inner ** 2 + offset ** 2) / (2 * offset)
    y = math.sqrt(0.25 - x * x)
    a, b = math.atan2(y, x), math.atan2(y, x - offset)
    cr.arc(0, 0, 0.5, a, 2 * math.pi - a)
    cr.arc_negative(offset, 0, inner, 2 * math.pi - b, b)
    cr.close_path()


def _ring(cr, r=0.36):
    cr.arc(0, 0, 0.5, 0, 2 * math.pi)
    cr.new_sub_path()
    cr.arc(0, 0, r, 0, 2 * math.pi)


def _wave(cr, amp=0.12, band=0.22):
    top = [(i / 40 - 0.5, -band / 2 + amp * math.sin((i / 40 - 0.5) * 4 * math.pi)) for i in range(41)]
    _polygon(cr, top + [(x, y + band) for x, y in reversed(top)])


def _cog(cr, teeth=10, r=0.5, root=0.4, hole=0.18):
    points = []
    for i in range(teeth):
        for step, radius in ((-0.3, root), (-0.18, r), (0.18, r), (0.3, root)):
            angle = 2 * math.pi * (i + step) / teeth
            points.append((radius * math.cos(angle), radius * math.sin(angle)))
    _polygon(cr, points)
    cr.new_sub_path()
    cr.arc(0, 0, hole, 0, 2 * math.pi)


SHAPES = {
    "Circle": lambda cr: cr.arc(0, 0, 0.5, 0, 2 * math.pi),
    "Ring": _ring,
    "Half circle": lambda cr: (cr.arc(0, 0.25, 0.5, math.pi, 2 * math.pi), cr.close_path()),
    "Quarter": lambda cr: (cr.move_to(-.5, .5), cr.arc(-.5, .5, 1, -math.pi / 2, 0), cr.close_path()),
    "Crescent": _crescent,
    "Square": lambda cr: cr.rectangle(-.5, -.5, 1, 1),
    "Frame": lambda cr: (cr.rectangle(-.5, -.5, 1, 1), cr.rectangle(-.36, -.36, .72, .72)),
    "Diamond": lambda cr: _regular(cr, 4),
    "Triangle": lambda cr: _polygon(cr, [(0, -.5), (.5, .5), (-.5, .5)]),
    "Corner": lambda cr: _polygon(cr, [(-.5, -.5), (.5, .5), (-.5, .5)]),
    "Hexagon": lambda cr: _regular(cr, 6),
    "Hex ring": lambda cr: (_regular(cr, 6), _regular(cr, 6, r=0.36)),
    "Pentagon": lambda cr: _regular(cr, 5),
    "Octagon": lambda cr: _regular(cr, 8, start=-22.5),
    "Star": lambda cr: _regular(cr, 5, inner=0.2),
    "Six star": lambda cr: _regular(cr, 6, inner=0.29),
    "Burst": lambda cr: _regular(cr, 12, inner=0.36),
    "Cross": _cross,
    "Saltire": _saltire,
    "Chevron": lambda cr: _polygon(cr, [(-.5, .15), (0, -.35), (.5, .15), (.5, .45), (0, -.05), (-.5, .45)]),
    "Arrow": lambda cr: _polygon(cr, [(-.5, -.15), (.1, -.15), (.1, -.4), (.5, 0), (.1, .4), (.1, .15), (-.5, .15)]),
    "Bar": lambda cr: cr.rectangle(-.5, -.12, 1, .24),
    "Half": lambda cr: cr.rectangle(-.5, 0, 1, .5),
    "Wave": _wave,
    "Cog": _cog,
}


# --- images

@lru_cache(maxsize=1024)
def _load(path, mtime, opener):
    return opener(path).convert("RGBA")


def load_image(path, opener):
    """Cached; don't modify the result in place. opener: the app's open_image (fast DDS)."""
    path = Path(path)
    return _load(str(path), path.stat().st_mtime, opener)


def cairo_surface(img, device_scale=1):
    data = bytearray(img.convert("RGBA").tobytes("raw", "BGRa"))
    surface = cairo.ImageSurface.create_for_data(data, cairo.FORMAT_ARGB32, img.width, img.height, img.width * 4)
    surface.set_device_scale(device_scale, device_scale)
    return surface


def from_cairo(surface):
    surface.flush()
    return Image.frombuffer("RGBA", (surface.get_width(), surface.get_height()), bytes(surface.get_data()),
                            "raw", "BGRa", surface.get_stride(), 1)


def colourize(channels, primary, secondary):
    """What the flag shader does to a background: primary x red channel + secondary x green."""
    r, g, _ = channels.convert("RGB").split()
    return Image.merge("RGB", [ImageChops.add(r.point(lambda v, k=p: v * k // 255), g.point(lambda v, k=s: v * k // 255))
                               for p, s in zip(primary, secondary)])


def render_flag(core, game, background, emblem, size, full=False):
    """A flag as the flag editor draws it (see core.compose_flag)."""
    return core.compose_flag(game, background, emblem, size, full)


def hexagon(left, top, right, bottom):
    """Corners of the flag's pointy-top hexagon from its bounding box."""
    cx, q = (left + right) / 2, (bottom - top) / 4
    return [(cx, top), (right, top + q), (right, bottom - q), (cx, bottom), (left, bottom - q), (left, top + q)]


# --- game and mod data

class GameData:
    """Flag parts from the game plus this mod: colours, backgrounds and emblems."""

    def __init__(self, core, game):
        self.core, self.game = core, game
        self.reload()

    def reload(self):
        core, game = self.core, self.game
        loc = (game / "localisation/english/main_1_l_english.yml").read_text(encoding="utf-8-sig") if game else ""
        colour_names = dict(re.findall(r'^\s*FLAG_COLOR_(\w+):\d*\s*"([^"\\]*)', loc, re.M))
        self.category_names = dict(re.findall(r'^\s*FLAG_CATEGORY_(\w+):\d*\s*"([^"]*)"', loc, re.M))
        self.category_names["custom_flags"] = "Custom"

        self.colours = [(c["key"], c["name"], tuple(c["rgb"])) for c in core.load_colours()]
        if game:
            text = (game / "flags/colors.txt").read_text(encoding="utf-8")
            for key, r, g, b in re.findall(r"^\s*(\w+)\s*=\s*\{\s*flag\s*=\s*rgb\s*\{\s*(\d+)\s+(\d+)\s+(\d+)", text, re.M):
                self.colours.append((key, colour_names.get(key, key.replace("_", " ").title()), (int(r), int(g), int(b))))

        self.backgrounds = sorted((game / "flags/backgrounds").glob("*.dds")) if game else []
        self.backgrounds += sorted(background_dir(core).glob("*.dds"))

        self.categories, self.all_categories = {}, {}  # shown in the game's flag editor / every one
        custom = sorted(core.OUT.glob("*.dds"))
        if custom:
            self.categories["custom_flags"] = self.all_categories["custom_flags"] = custom
        for folder in sorted((game / "flags").iterdir()) if game else []:
            files = sorted(folder.glob("*.dds")) if folder.is_dir() and folder.name != "backgrounds" else []
            if files:
                self.all_categories[folder.name] = files
                usage = folder / "usage.txt"
                if not (usage.exists() and re.search(r"show_in_designer\s*=\s*no", usage.read_text())):
                    self.categories[folder.name] = files

    def colour(self, key):
        return next((c for c in self.colours if c[0] == key), None)

    def category_label(self, category):
        return self.category_names.get(category, category.replace("_", " ").title())

    def emblem_path(self, ref):
        """"category/name" -> the emblem's 128px file."""
        category, _, stem = ref.partition("/")
        folder = self.core.OUT if category == "custom_flags" else self.game / "flags" / category
        return folder / f"{stem}.dds"


def background_dir(core):
    return core.HERE / "flags" / "backgrounds"


def projects_dir(core):
    return core.HERE / "maker"


def slug(text):
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "design"


# --- the Maker's layers

def new_layer(shape, kind, base):
    layer = {"shape": shape, "x": 0.5, "y": 0.5, "size": 0.5, "stretch": 0.0, "rot": 0.0,
             "flip_x": False, "flip_y": False, "opacity": 1.0}
    if kind == "background":
        layer["slot"] = "secondary" if base == "primary" else "primary"
    else:
        layer["rgb"] = [255, 255, 255]
        layer["original"] = shape not in SHAPES
    return layer


def layer_matrix(layer, size):
    m = cairo.Matrix()
    m.translate(layer["x"] * size, layer["y"] * size)
    m.rotate(math.radians(layer["rot"]))
    w, h = max(layer["size"] * 2 ** layer["stretch"], 0.01), max(layer["size"], 0.01)
    m.scale(w * size * (-1 if layer["flip_x"] else 1), h * size * (-1 if layer["flip_y"] else 1))
    return m


def hit(layer, x, y, size):
    m = layer_matrix(layer, size)
    m.invert()
    u, v = m.transform_point(x, y)
    return abs(u) <= 0.5 and abs(v) <= 0.5


def shape_image(data, key, original):
    """Picture used by an "emblem:category/name" or "upload:name" layer."""
    kind, _, ref = key.partition(":")
    core = data.core
    if kind == "emblem":
        path = data.emblem_path(ref)
        silhouette = path.parent / "map" / path.name  # vanilla map emblems are white silhouettes at 256px
        return load_image(silhouette if not original and silhouette.exists() else path, data.core.open_image)
    if kind == "upload":
        path = core.source(ref)
        return _upload_square(str(path), path.stat().st_mtime, core.load_manifest().get(ref, {}).get("fit", False), core.square)
    raise ValueError(f"unknown shape {key}")


@lru_cache(maxsize=64)
def _upload_square(path, mtime, fit, square):
    return square(Image.open(path).convert("RGBA"), 512, fit)


def render_project(data, project, size, colour_of):
    """Draw a Maker project at size x size. colour_of(layer) gives a layer's RGB;
    colour_of(None) the background's base."""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    if project["kind"] == "background":
        cr.set_source_rgb(*(c / 255 for c in colour_of(None)))
        cr.paint()
    for layer in project["layers"]:
        r, g, b = (c / 255 for c in colour_of(layer))
        cr.save()
        cr.transform(layer_matrix(layer, size))
        if layer["shape"] in SHAPES:
            cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
            SHAPES[layer["shape"]](cr)
            cr.set_source_rgba(r, g, b, layer["opacity"])
            cr.fill()
        else:
            try:
                img = shape_image(data, layer["shape"], layer.get("original", False))
            except (OSError, ValueError):
                img = None  # its picture was removed; skip the layer
            if img is not None:
                source = cairo_surface(img)
                cr.scale(1 / img.width, 1 / img.height)
                cr.translate(-img.width / 2, -img.height / 2)
                if layer.get("original"):
                    cr.set_source_surface(source, 0, 0)
                    cr.paint_with_alpha(layer["opacity"])
                else:
                    cr.set_source_rgba(r, g, b, layer["opacity"])
                    cr.mask_surface(source, 0, 0)
        cr.restore()
    return from_cairo(surface)


def shape_thumb(data, key, size, rgb=(230, 245, 240)):
    project = {"kind": "emblem", "layers": [dict(new_layer(key, "emblem", None), size=0.9, rgb=list(rgb), original=False)]}
    return render_project(data, project, size, lambda layer: tuple(layer["rgb"]))


def load_projects(core, kind):
    found = []
    for path in sorted(projects_dir(core).glob(f"{kind}_*.json")):
        try:
            found.append(json.loads(path.read_text()))
        except ValueError:
            pass
    return sorted(found, key=lambda p: p["name"].lower())


# --- small UI helpers

def ask(ui, title, text, ok):
    dialog = Gtk.Dialog(transient_for=ui.win, modal=True, decorated=not ui.themed)
    ui.styled(dialog, "sw-window")
    box = dialog.get_content_area()
    box.set_spacing(10)
    box.set_border_width(18)
    box.pack_start(ui.styled(Gtk.Label(label=title, xalign=0), "sw-name"), False, False, 0)
    if text:
        box.pack_start(Gtk.Label(label=text, xalign=0, wrap=True, max_width_chars=50), False, False, 0)
    ui.styled(dialog.add_button("Cancel", Gtk.ResponseType.CANCEL), "sw-btn")
    ui.styled(dialog.add_button(ok, Gtk.ResponseType.OK), "sw-btn")
    dialog.get_action_area().set_border_width(12)
    dialog.show_all()
    answer = dialog.run() == Gtk.ResponseType.OK
    dialog.destroy()
    return answer


def section(ui, text):
    return ui.styled(Gtk.Label(label=text, xalign=0), "sw-section")


def flowbox(per_row):
    return Gtk.FlowBox(selection_mode=Gtk.SelectionMode.SINGLE, max_children_per_line=per_row,
                       min_children_per_line=per_row, homogeneous=True, valign=Gtk.Align.START,
                       activate_on_single_click=True, row_spacing=2, column_spacing=2)


def scrolled(child, width, height, ui=None):
    box = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
    box.set_size_request(width, height)
    box.add(child)
    if ui:
        panel = ui.styled(Gtk.Box(), "sw-dark")
        panel.pack_start(box, True, True, 0)
        return panel
    return box


def swatch(rgb, size):
    im = Image.new("RGBA", (size, size), tuple(rgb) + (255,))
    im.paste((255, 255, 255, 90), (0, 0, size, max(1, size // 20)))
    return im


def selected_index(box):
    children = box.get_selected_children()
    return children[0].get_index() if children else None


class ColourPicker(Gtk.Box):
    """The classic picker from the Colours tab: saturation/brightness square, hue bar,
    and a box for #RRGGBB or rgb(r, g, b)."""
    RING = 8

    def __init__(self, core, on_change, width=240, height=130):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.core, self.on_change = core, on_change
        self.hsv, self.busy = [0.0, 0.0, 100.0], False
        self.square, self.bar = Gtk.DrawingArea(), Gtk.DrawingArea()
        self.square.set_size_request(width, height)
        self.bar.set_size_request(width, 22)
        for area, draw, pick in ((self.square, self.draw_square, self.pick_square), (self.bar, self.draw_bar, self.pick_bar)):
            area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON1_MOTION_MASK)
            area.connect("draw", draw)
            area.connect("button-press-event", pick)
            area.connect("motion-notify-event", pick)
            self.pack_start(area, False, False, 0)
        self.code = Gtk.Entry(max_length=24, width_chars=11, tooltip_text="#FF8800 or rgb(255, 136, 0)")
        self.code.connect("changed", self.typed)
        self.pack_start(self.code, False, False, 0)

    def set_rgb(self, rgb):
        self.hsv = self.core.rgb_to_hsv(rgb)
        self.busy = True
        self.code.set_text(self.core.hex_code(rgb))
        self.busy = False
        self.queue_draw()

    def changed(self, rgb, from_code=False):
        if not from_code:
            self.busy = True
            self.code.set_text(self.core.hex_code(rgb))
            self.busy = False
        self.queue_draw()
        self.on_change(list(rgb))

    def box(self, area):
        return self.RING, self.RING, area.get_allocated_width() - 2 * self.RING, area.get_allocated_height() - 2 * self.RING

    def draw_square(self, area, cr):
        x0, y0, w, h = self.box(area)
        cr.save()
        cr.translate(x0, y0)
        cr.rectangle(0, 0, w, h)
        cr.clip()
        cr.set_source_rgb(*(c / 255 for c in self.core.hsv_to_rgb(self.hsv[0], 100, 100)))
        cr.paint()
        for x1, y1, start, end in ((w, 0, (1, 1, 1, 1), (1, 1, 1, 0)), (0, h, (0, 0, 0, 0), (0, 0, 0, 1))):
            gradient = cairo.LinearGradient(0, 0, x1, y1)
            gradient.add_color_stop_rgba(0, *start)
            gradient.add_color_stop_rgba(1, *end)
            cr.set_source(gradient)
            cr.paint()
        cr.restore()
        x, y = x0 + self.hsv[1] / 100 * w, y0 + (1 - self.hsv[2] / 100) * h
        cr.set_line_width(1.5)
        for colour, radius in (((0, 0, 0), 7), ((1, 1, 1), 5.5)):
            cr.set_source_rgb(*colour)
            cr.arc(x, y, radius, 0, 2 * math.pi)
            cr.stroke()

    def draw_bar(self, area, cr):
        x0, h = self.RING, area.get_allocated_height()
        w = area.get_allocated_width() - 2 * self.RING
        gradient = cairo.LinearGradient(x0, 0, x0 + w, 0)
        for i in range(7):
            gradient.add_color_stop_rgb(i / 6, *(c / 255 for c in self.core.hsv_to_rgb(i * 60, 100, 100)))
        cr.rectangle(x0, 4, w, h - 8)
        cr.set_source(gradient)
        cr.fill()
        x = x0 + self.hsv[0] / 360 * w
        cr.rectangle(x - 3.5, 0.5, 7, h - 1)
        cr.set_source_rgb(233 / 255, 1, 251 / 255)
        cr.fill_preserve()
        cr.set_source_rgb(*CYAN)
        cr.set_line_width(1)
        cr.stroke()

    @staticmethod
    def picking(event):
        if event.type == Gdk.EventType.BUTTON_PRESS:
            return event.button == 1
        return bool(event.state & Gdk.ModifierType.BUTTON1_MASK)

    def pick_square(self, area, event):
        if self.picking(event):
            x0, y0, w, h = self.box(area)
            self.hsv[1] = min(max((event.x - x0) / w, 0), 1) * 100
            self.hsv[2] = (1 - min(max((event.y - y0) / h, 0), 1)) * 100
            self.changed(self.core.hsv_to_rgb(*self.hsv))
        return True

    def pick_bar(self, area, event):
        if self.picking(event):
            self.hsv[0] = min(max((event.x - self.RING) / (area.get_allocated_width() - 2 * self.RING), 0), 1) * 360
            self.changed(self.core.hsv_to_rgb(*self.hsv))
        return True

    def typed(self, _):
        rgb = self.core.parse_colour_code(self.code.get_text())
        if rgb and not self.busy:
            self.hsv = self.core.rgb_to_hsv(rgb)
            self.changed(rgb, from_code=True)


# --- Designer tab

class DesignerPage:
    """Plan a flag: background, primary and secondary colour and emblem, drawn the way
    the game's flag editor draws them. Remembered in designer.json; the Maker uses the
    same colours and emblem for its previews."""

    def __init__(self, ui, data):
        self.ui, self.data = ui, data
        core = data.core
        self.path = core.HERE / "designer.json"
        try:
            self.design = json.loads(self.path.read_text())
        except (OSError, ValueError):
            self.design = {}
        ui.designer = self
        ui.design_colours = self.colours
        self.busy = False

        self.widget = Gtk.Box(spacing=20)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.preview = Gtk.Image()
        left.pack_start(self.preview, False, False, 0)
        self.summary = Gtk.Label(wrap=True, max_width_chars=36, justify=Gtk.Justification.CENTER, use_markup=True)
        left.pack_start(self.summary, False, False, 0)
        self.widget.pack_start(left, False, False, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.widget.pack_start(right, True, True, 0)
        right.pack_start(section(ui, "Background"), False, False, 0)
        self.bg_grid = flowbox(6)
        right.pack_start(scrolled(self.bg_grid, 6 * (THUMB + 10), 112, ui), False, False, 0)
        right.pack_start(section(ui, "Colours"), False, False, 0)
        colour_row = Gtk.Box(spacing=8)
        self.colour_buttons = {}
        for slot in ("primary", "secondary"):
            button = ui.styled(Gtk.Button(), "sw-btn", "sw-small")
            content = Gtk.Box(spacing=6)
            content.pack_start(Gtk.Image(), False, False, 0)
            content.pack_start(Gtk.Label(ellipsize=Pango.EllipsizeMode.END, max_width_chars=12), False, False, 0)
            button.add(content)
            button.connect("clicked", self.pick_colour, slot)
            colour_row.pack_start(button, True, True, 0)
            self.colour_buttons[slot] = button
        right.pack_start(colour_row, False, False, 0)
        right.pack_start(section(ui, "Emblem"), False, False, 0)
        self.categories = Gtk.ComboBoxText()
        right.pack_start(self.categories, False, False, 0)
        self.emblem_grid = flowbox(6)
        right.pack_start(scrolled(self.emblem_grid, 6 * (THUMB + 10), 150, ui), False, False, 0)

        self.bg_grid.connect("selected-children-changed", self.picked_background)
        self.emblem_grid.connect("selected-children-changed", self.picked_emblem)
        self.categories.connect("changed", lambda _: None if self.busy else self.fill_emblems())
        self.refresh()

    # design values, with fallbacks when something was removed
    def colour(self, slot):
        found = self.data.colour(self.design.get(slot))
        if not found:
            fallback = {"primary": "turquoise", "secondary": "black"}[slot]
            found = self.data.colour(fallback) or (fallback, fallback.title(), (128, 128, 128))
        return found

    def colours(self):
        return {"primary": self.colour("primary")[2], "secondary": self.colour("secondary")[2], "black": (0, 0, 0)}

    def background_path(self):
        by_name = {p.name: p for p in self.data.backgrounds}
        return by_name.get(self.design.get("background")) or by_name.get("00_solid.dds") or \
            (self.data.backgrounds[0] if self.data.backgrounds else None)

    def background_image(self, path=None, size=None):
        path = path or self.background_path()
        channels = load_image(path, self.data.core.open_image) if path else Image.new("RGB", (8, 8), (255, 0, 0))
        if size:
            channels = channels.resize((size, size), Image.LANCZOS)
        c = self.colours()
        return colourize(channels, c["primary"], c["secondary"])

    def emblem_full(self):
        """Whether the chosen emblem is one of yours set to fill the whole flag."""
        category, _, stem = (self.design.get("emblem") or "").partition("/")
        info = self.data.core.load_manifest().get(stem) if category == "custom_flags" else None
        return bool(info) and self.data.core.settings(info)["full"]

    def emblem_image(self):
        ref = self.design.get("emblem")
        try:
            return load_image(self.data.emblem_path(ref), self.data.core.open_image) if ref else None
        except OSError:
            return None

    def save(self):
        self.path.write_text(json.dumps(self.design, indent=1))

    # UI
    def refresh(self):
        """Reload everything; other tabs may have added colours, emblems or backgrounds."""
        self.data.reload()
        self.busy = True
        self.categories.remove_all()
        for category in self.data.categories:
            self.categories.append(category, self.data.category_label(category))
        current = (self.design.get("emblem") or "").partition("/")[0]
        first = current if current in self.data.categories else next(iter(self.data.categories), None)
        if first:
            self.categories.set_active_id(first)
        self.busy = False
        self.fill_backgrounds()
        self.fill_emblems()
        self.update()

    def fill_backgrounds(self):
        self.busy = True
        for child in self.bg_grid.get_children():
            self.bg_grid.remove(child)
        current = self.background_path()
        for path in self.data.backgrounds:
            tile = Gtk.Image(tooltip_text=path.stem)
            self.ui.show_image(tile, self.background_image(path, THUMB * self.ui.scale))
            self.bg_grid.add(tile)
            if path == current:
                self.bg_grid.select_child(self.bg_grid.get_children()[-1])
        self.bg_grid.show_all()
        self.busy = False

    def fill_emblems(self):
        self.busy = True
        for child in self.emblem_grid.get_children():
            self.emblem_grid.remove(child)
        category = self.categories.get_active_id()
        for path in self.data.categories.get(category, []):
            tile = Gtk.Image(tooltip_text=path.stem)
            self.ui.show_image(tile, load_image(path, self.data.core.open_image).resize((THUMB * self.ui.scale,) * 2, Image.LANCZOS))
            self.emblem_grid.add(tile)
            if self.design.get("emblem") == f"{category}/{path.stem}":
                self.emblem_grid.select_child(self.emblem_grid.get_children()[-1])
        self.emblem_grid.show_all()
        self.busy = False

    def update(self):
        ui = self.ui
        ui.show_image(self.preview, render_flag(self.data.core, self.data.game, self.background_image(),
                                                self.emblem_image(), DESIGN_PREVIEW * ui.scale, self.emblem_full()))
        for slot, button in self.colour_buttons.items():
            key, name, rgb = self.colour(slot)
            icon, label = button.get_child().get_children()
            ui.show_image(icon, swatch(rgb, 18 * ui.scale))
            label.set_text(name)
            button.set_tooltip_text(f"{SLOTS[slot]} colour: {name}")
        background = self.background_path()
        ref = self.design.get("emblem")
        emblem = f"{self.data.category_label(ref.partition('/')[0])} › {ref.partition('/')[2]}" if ref else "none"
        self.summary.set_markup(
            f"<span foreground='{GREY}'>To use it, pick these in the game's flag editor:</span>\n"
            f"<span foreground='{ORANGE}'>Background</span> {GLib.markup_escape_text(background.stem if background else '-')}\n"
            f"<span foreground='{ORANGE}'>Primary</span> {GLib.markup_escape_text(self.colour('primary')[1])}   "
            f"<span foreground='{ORANGE}'>Secondary</span> {GLib.markup_escape_text(self.colour('secondary')[1])}\n"
            f"<span foreground='{ORANGE}'>Emblem</span> {GLib.markup_escape_text(emblem)}")

    def picked_background(self, _):
        index = selected_index(self.bg_grid)
        if not self.busy and index is not None:
            self.design["background"] = self.data.backgrounds[index].name
            self.save()
            self.update()

    def picked_emblem(self, _):
        index = selected_index(self.emblem_grid)
        category = self.categories.get_active_id()
        if not self.busy and index is not None:
            self.design["emblem"] = f"{category}/{self.data.categories[category][index].stem}"
            self.save()
            self.update()

    def pick_colour(self, button, slot):
        """A popover with every colour the game offers (yours first), 9 to a row like in game."""
        popover = Gtk.Popover(relative_to=button, position=Gtk.PositionType.BOTTOM)
        grid = flowbox(9)
        for key, name, rgb in self.data.colours:
            tile = Gtk.Image(tooltip_text=name)
            self.ui.show_image(tile, swatch(rgb, 22 * self.ui.scale))
            grid.add(tile)
        keys = [c[0] for c in self.data.colours]
        if self.colour(slot)[0] in keys:
            grid.select_child(grid.get_child_at_index(keys.index(self.colour(slot)[0])))

        def chosen(box, child):
            self.design[slot] = keys[child.get_index()]
            self.save()
            popover.popdown()
            self.fill_backgrounds()
            self.update()
        grid.connect("child-activated", chosen)
        popover.add(grid)
        grid.show_all()
        popover.popup()


# --- Maker tab

class Editor:
    """One Maker mode ("background" or "emblem"): project bar, canvas, layer list and the
    selected layer's settings."""

    def __init__(self, ui, data, kind):
        self.ui, self.data, self.kind = ui, data, kind
        self.core = data.core
        self.project = self.blank()
        self.selected = None
        self.dirty = False
        self.busy = False
        self.drag = None
        self.status_text = ""
        self.pending_preview = None
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        bar = Gtk.Box(spacing=8)
        self.projects = Gtk.ComboBoxText(tooltip_text=f"Your saved {kind}s")
        self.name_entry = Gtk.Entry(placeholder_text=f"{kind.title()} name", max_length=40)
        save_btn = ui.styled(Gtk.Button(label="Save"), "sw-btn", "sw-small")
        delete_btn = ui.styled(Gtk.Button(label="Delete"), "sw-btn", "sw-small")
        bar.pack_start(self.projects, False, False, 0)
        bar.pack_start(self.name_entry, True, True, 0)
        bar.pack_start(save_btn, False, False, 0)
        bar.pack_start(delete_btn, False, False, 0)
        self.widget.pack_start(bar, False, False, 0)

        body = Gtk.Box(spacing=16)
        self.widget.pack_start(body, True, True, 0)
        left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        body.pack_start(left, False, False, 0)
        self.canvas = Gtk.DrawingArea(tooltip_text="Drag to move. Wheel: size, Shift+wheel: rotate, Ctrl+wheel: stretch")
        self.canvas.set_size_request(MAKER_CANVAS, MAKER_CANVAS)
        self.canvas.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON1_MOTION_MASK
                               | Gdk.EventMask.SCROLL_MASK | Gdk.EventMask.SMOOTH_SCROLL_MASK)
        left.pack_start(self.canvas, False, False, 0)
        under = Gtk.Box(spacing=10)
        self.preview = Gtk.Image(tooltip_text="On your Designer flag")
        self.status = Gtk.Label(wrap=True, max_width_chars=22, xalign=0, use_markup=True)
        under.pack_start(self.preview, False, False, 0)
        under.pack_start(self.status, True, True, 0)
        left.pack_start(under, False, False, 0)

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        body.pack_start(right, True, True, 0)
        self.slot_buttons = {}
        if kind == "background":
            right.pack_start(section(ui, "Base colour"), False, False, 0)
            right.pack_start(self.slot_row("base"), False, False, 0)
        if kind == "emblem":
            right.pack_start(section(ui, "Canvas"), False, False, 0)
            canvas_row = Gtk.Box(spacing=12)
            self.full_btn = Gtk.CheckButton(label="Fill the whole flag",
                                            tooltip_text="Design over the whole flag instead of the emblem square in its middle")
            self.spill_btn = Gtk.CheckButton(label="Spill over the border",
                                             tooltip_text="Draw anything past the flag's edge over its border instead of cutting it off at the hexagon")
            self.full_btn.connect("toggled", lambda b: self.set_canvas("full", b.get_active()))
            self.spill_btn.connect("toggled", lambda b: self.set_canvas("cut", not b.get_active()))
            canvas_row.pack_start(self.full_btn, False, False, 0)
            canvas_row.pack_start(self.spill_btn, False, False, 0)
            right.pack_start(canvas_row, False, False, 0)
        right.pack_start(section(ui, "Layers"), False, False, 0)
        buttons = Gtk.Box(spacing=4, homogeneous=True)
        self.add_btn = ui.styled(Gtk.Button(label="Add"), "sw-btn", "sw-small")
        self.layer_buttons = {}
        for label in ("Add", "Copy", "Up", "Down", "Delete"):
            button = self.add_btn if label == "Add" else ui.styled(Gtk.Button(label=label), "sw-btn", "sw-small")
            buttons.pack_start(button, True, True, 0)
            self.layer_buttons[label] = button
        right.pack_start(buttons, False, False, 0)
        self.layer_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        right.pack_start(scrolled(self.layer_list, 300, 118, ui), False, False, 0)

        self.props = Gtk.Grid(column_spacing=8, row_spacing=4)
        row = 0
        if kind == "background":
            self.props.attach(Gtk.Label(label="Colour", xalign=0), 0, row, 1, 1)
            self.props.attach(self.slot_row("layer"), 1, row, 1, 1)
        else:
            self.colour_btn = ui.styled(Gtk.Button(), "sw-btn", "sw-small")
            self.colour_btn.add(Gtk.Image())
            self.colour_btn.connect("clicked", self.pick_colour)
            self.original = Gtk.CheckButton(label="Original colours", no_show_all=True,
                                            tooltip_text="Keep the picture's own colours instead of one colour")
            self.dropper = self.core.dropper_button(ui.styled)
            self.dropper.connect("clicked", self.use_dropper)
            colour_box = Gtk.Box(spacing=8)
            colour_box.pack_start(self.colour_btn, False, False, 0)
            colour_box.pack_start(self.dropper, False, False, 0)
            colour_box.pack_start(self.original, False, False, 0)
            self.props.attach(Gtk.Label(label="Colour", xalign=0), 0, row, 1, 1)
            self.props.attach(colour_box, 1, row, 1, 1)
            self.original.connect("toggled", lambda b: self.set_prop("original", b.get_active()))
        self.sliders = {}
        for label, prop, lo, hi, step in (("Size", "size", 0.02, 1.6, 0.01), ("Stretch", "stretch", -2, 2, 0.01),
                                          ("Rotate", "rot", -180, 180, 1), ("Opacity", "opacity", 0, 1, 0.01)):
            row += 1
            slider = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, step)
            slider.set_draw_value(False)
            slider.set_hexpand(True)
            slider.connect("value-changed", lambda s, p=prop: self.set_prop(p, s.get_value()))
            self.props.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
            self.props.attach(slider, 1, row, 1, 1)
            self.sliders[prop] = slider
        row += 1
        flips = Gtk.Box(spacing=8)
        self.flip = {}
        for label, prop in (("Flip ↔", "flip_x"), ("Flip ↕", "flip_y")):
            toggle = ui.styled(Gtk.ToggleButton(label=label), "sw-btn", "sw-small", "sw-tab")
            toggle.connect("toggled", lambda b, p=prop: self.set_prop(p, b.get_active()))
            flips.pack_start(toggle, True, True, 0)
            self.flip[prop] = toggle
        self.props.attach(flips, 1, row, 1, 1)
        right.pack_start(self.props, False, False, 0)

        self.canvas.connect("draw", self.draw)
        self.canvas.connect("button-press-event", self.press)
        self.canvas.connect("motion-notify-event", self.motion)
        self.canvas.connect("scroll-event", self.scroll)
        self.layer_list.connect("row-selected", self.row_selected)
        self.projects.connect("changed", self.open_project)
        self.name_entry.connect("changed", lambda e: None if self.busy else self.mark_dirty())
        save_btn.connect("clicked", self.save)
        delete_btn.connect("clicked", self.delete)
        self.add_btn.connect("clicked", self.add_popover)
        self.layer_buttons["Copy"].connect("clicked", lambda _: self.copy_layer())
        self.layer_buttons["Up"].connect("clicked", lambda _: self.move_layer(1))
        self.layer_buttons["Down"].connect("clicked", lambda _: self.move_layer(-1))
        self.layer_buttons["Delete"].connect("clicked", lambda _: self.delete_layer())
        self.fill_projects()
        self.load(self.project)

    # project
    def blank(self):
        project = {"kind": self.kind, "name": "", "layers": []}
        if self.kind == "emblem":  # new emblems can use the whole flag and spill past its border
            project.update(full=True, cut=True)
        if self.kind == "background":
            project["base"] = "primary"
        return project

    def fill_projects(self, select=None):
        self.busy = True
        self.projects.remove_all()
        self.projects.append("", f"New {self.kind}")
        for project in load_projects(self.core, self.kind):
            self.projects.append(project["id"], project["name"])
        self.projects.set_active_id(select or "")
        self.busy = False

    def open_project(self, _):
        if self.busy:
            return
        pid = self.projects.get_active_id()
        if self.dirty and not ask(self.ui, "Discard unsaved changes?", "Your changes to this design haven't been saved.", "Discard"):
            self.fill_projects(self.project.get("id"))
            return
        project = next((p for p in load_projects(self.core, self.kind) if p["id"] == pid), None) if pid else None
        self.load(project or self.blank())

    def load(self, project):
        self.project = project
        self.selected = len(project["layers"]) - 1 if project["layers"] else None
        self.busy = True
        self.name_entry.set_text(project["name"])
        self.busy = False
        self.dirty = False
        self.status_text = ""
        self.fill_layers()
        self.changed(dirty=False)

    def canvas_options(self):
        """(full, cut) for an emblem project; older projects keep the plain emblem square."""
        return self.project.get("full", False), self.project.get("cut", True)

    def set_canvas(self, key, value):
        if not self.busy:
            self.project[key] = value
            self.show_props()
            self.changed()

    def mark_dirty(self):
        self.dirty = True
        self.show_status()

    def output_name(self):
        """The background file or emblem name, fixed at the first save."""
        return self.project.get("output")

    def save(self, _=None):
        core, project = self.core, self.project
        project["name"] = self.name_entry.get_text().strip() or project["name"] or f"My {self.kind}"
        taken = {p["id"] for p in load_projects(core, self.kind)}
        if "id" not in project:
            base, n = slug(project["name"]), 2
            project["id"] = base
            while project["id"] in taken:
                project["id"], n = f"{base}_{n}", n + 1
        try:
            if self.kind == "background":
                project.setdefault("output", f"cf_bg_{project['id']}")
                image = render_project(self.data, project, 400, lambda layer: CHANNELS[layer["slot"] if layer else project["base"]])
                background_dir(core).mkdir(parents=True, exist_ok=True)
                (background_dir(core) / f"{project['output']}.dds").write_bytes(core.dds_bytes([image]))
            else:
                manifest = core.load_manifest()
                if "output" not in project:
                    base, n = slug(project["name"]), 2
                    project["output"] = base
                    while project["output"] in manifest:
                        project["output"], n = f"{base}_{n}", n + 1
                name = project["output"]
                image = render_project(self.data, project, 512, lambda layer: tuple(layer["rgb"]))
                core.UPLOADS.mkdir(exist_ok=True)
                image.save(core.source(name))
                info = manifest.get(name, {"fit": False, "white_map": core.has_transparency(image)})
                info["full"], info["cut"] = self.canvas_options()
                manifest[name] = info
                core.save_manifest(manifest)
                core.render(name, **core.settings(info))
                core.write_flag_shader(self.data.game)
            projects_dir(core).mkdir(exist_ok=True)
            (projects_dir(core) / f"{self.kind}_{project['id']}.json").write_text(json.dumps(project, indent=1))
        except Exception as e:
            self.status_text = f"<span foreground='{RED}'>Couldn't save: {GLib.markup_escape_text(str(e))}</span>"
            self.show_status()
            return
        self.dirty = False
        where = "the flag editor's backgrounds" if self.kind == "background" else "the Custom emblem category"
        self.status_text = f"<span foreground='{GREY}'>Saved. It's in {where}.</span>"
        if self.core.game_running():
            self.status_text += f"\n<span foreground='{RED}'>Restart Stellaris to see it.</span>"
        self.fill_projects(project["id"])
        self.show_status()

    def delete(self, _):
        project = self.project
        if "id" not in project:
            self.load(self.blank())
            return
        what = "background" if self.kind == "background" else "emblem"
        if not ask(self.ui, f"Delete {project['name']}?", f"Any empire using this {what} will lose it.", "Delete"):
            return
        core = self.core
        (projects_dir(core) / f"{self.kind}_{project['id']}.json").unlink(missing_ok=True)
        if self.kind == "background":
            (background_dir(core) / f"{project['output']}.dds").unlink(missing_ok=True)
        elif project.get("output") in core.load_manifest():
            core.remove(project["output"])
        self.fill_projects()
        self.load(self.blank())

    # layers
    def layer(self):
        layers = self.project["layers"]
        return layers[self.selected] if self.selected is not None and self.selected < len(layers) else None

    def layer_colour(self, layer):
        colours = self.ui.design_colours()
        if self.kind == "background":
            return colours[layer["slot"] if layer else self.project["base"]]
        return tuple(layer["rgb"])

    def layer_name(self, layer):
        kind, _, ref = layer["shape"].partition(":")
        return ref.rpartition("/")[2] if ref else kind

    def fill_layers(self):
        self.busy = True
        for row in self.layer_list.get_children():
            self.layer_list.remove(row)
        layers = self.project["layers"]
        for index in reversed(range(len(layers))):  # top layer first, like layer lists usually are
            layer = layers[index]
            row = Gtk.Box(spacing=8)
            thumb = Gtk.Image()
            preview_layer = dict(layer, x=0.5, y=0.5, size=0.9, stretch=0, rot=0, opacity=1)
            project = {"kind": "emblem", "layers": [preview_layer]}
            self.ui.show_image(thumb, render_project(self.data, project, 22 * self.ui.scale, lambda l: self.layer_colour(l)))
            row.pack_start(thumb, False, False, 0)
            row.pack_start(Gtk.Label(label=f"{index + 1}. {self.layer_name(layer)}", xalign=0,
                                     ellipsize=Pango.EllipsizeMode.END), True, True, 0)
            self.layer_list.add(row)
        self.layer_list.show_all()
        if self.selected is not None and self.selected < len(layers):
            self.layer_list.select_row(self.layer_list.get_row_at_index(len(layers) - 1 - self.selected))
        self.busy = False
        self.show_props()

    def row_selected(self, _, row):
        if not self.busy and row is not None:
            self.selected = len(self.project["layers"]) - 1 - row.get_index()
            self.show_props()
            self.canvas.queue_draw()

    def show_props(self):
        layer = self.layer()
        self.busy = True
        for prop, slider in self.sliders.items():
            slider.set_value(layer[prop] if layer else slider.get_adjustment().get_lower())
        for prop, toggle in self.flip.items():
            toggle.set_active(bool(layer and layer[prop]))
        if self.kind == "background":
            for slot, button in self.slot_buttons.get("layer", {}).items():
                button.set_active(bool(layer) and layer["slot"] == slot)
            for slot, button in self.slot_buttons["base"].items():
                button.set_active(self.project["base"] == slot)
        else:
            full, cut = self.canvas_options()
            self.full_btn.set_active(full)
            self.spill_btn.set_active(not cut)
            self.spill_btn.set_sensitive(full)
            self.original.set_active(bool(layer and layer.get("original")))
            self.original.set_visible(bool(layer) and layer["shape"] not in SHAPES)
            if layer:
                self.ui.show_image(self.colour_btn.get_child(), swatch(layer["rgb"], 18 * self.ui.scale))
            self.colour_btn.set_sensitive(bool(layer) and not layer.get("original"))
            self.dropper.set_sensitive(bool(layer) and not layer.get("original"))
        self.busy = False
        self.props.set_sensitive(layer is not None)
        full = len(self.project["layers"]) >= MAX_LAYERS
        self.add_btn.set_sensitive(not full)
        self.layer_buttons["Copy"].set_sensitive(layer is not None and not full)
        for label in ("Up", "Down", "Delete"):
            self.layer_buttons[label].set_sensitive(layer is not None)

    def set_prop(self, prop, value):
        layer = self.layer()
        if self.busy or not layer:
            return
        layer[prop] = value
        if prop == "original":
            self.show_props()
        self.changed(relist=prop in ("original", "rgb", "slot"))

    def changed(self, dirty=True, relist=False):
        if dirty:
            self.dirty = True
        if relist:
            self.fill_layers()
        self.canvas.queue_draw()
        if not self.pending_preview:  # at most every 80 ms while dragging
            self.pending_preview = GLib.timeout_add(80, self.update_preview)
        self.show_status()

    def show_status(self):
        layers = len(self.project["layers"])
        text = self.status_text or f"<span foreground='{GREY}'>{layers} / {MAX_LAYERS} layers</span>"
        if self.dirty:
            text += f"\n<span foreground='{ORANGE}'>Not saved yet</span>"
        self.status.set_markup(text)

    def update_preview(self):
        self.pending_preview = None
        size = MAKER_PREVIEW * self.ui.scale
        designer = self.ui.designer
        if self.kind == "background":
            art = render_project(self.data, self.project, 400, self.layer_colour).convert("RGB")
            flag = render_flag(self.core, self.data.game, art, designer.emblem_image(), size, designer.emblem_full())
        else:
            art = render_project(self.data, self.project, 512, self.layer_colour)
            full, cut = self.canvas_options()
            if full and cut:
                art = self.core.hex_cut(art, True, self.data.game)
            flag = render_flag(self.core, self.data.game, designer.background_image(), art, size, full)
        self.ui.show_image(self.preview, flag)
        return False

    def add_layer(self, shape):
        layers = self.project["layers"]
        if len(layers) >= MAX_LAYERS:
            return
        layers.append(new_layer(shape, self.kind, self.project.get("base")))
        self.selected = len(layers) - 1
        self.fill_layers()
        self.changed()

    def copy_layer(self):
        layer = self.layer()
        if layer and len(self.project["layers"]) < MAX_LAYERS:
            copy = json.loads(json.dumps(layer))
            copy["x"], copy["y"] = min(copy["x"] + 0.04, 1), min(copy["y"] + 0.04, 1)
            self.project["layers"].insert(self.selected + 1, copy)
            self.selected += 1
            self.fill_layers()
            self.changed()

    def move_layer(self, step):
        layers, i = self.project["layers"], self.selected
        if i is not None and 0 <= i + step < len(layers):
            layers[i], layers[i + step] = layers[i + step], layers[i]
            self.selected = i + step
            self.fill_layers()
            self.changed()

    def delete_layer(self):
        if self.layer():
            del self.project["layers"][self.selected]
            self.selected = min(self.selected, len(self.project["layers"]) - 1) if self.project["layers"] else None
            self.fill_layers()
            self.changed()

    def slot_row(self, which):
        """Primary / Secondary / Black toggles, for the base or the selected layer."""
        row = Gtk.Box(spacing=4, homogeneous=True)
        self.slot_buttons[which] = {}
        for slot, label in SLOTS.items():
            toggle = self.ui.styled(Gtk.ToggleButton(label=label), "sw-btn", "sw-small", "sw-tab")
            toggle.connect("toggled", self.slot_toggled, which, slot)
            row.pack_start(toggle, True, True, 0)
            self.slot_buttons[which][slot] = toggle
        return row

    def slot_toggled(self, toggle, which, slot):
        if self.busy:
            return
        if not toggle.get_active():  # clicking the active one keeps it on
            self.show_props()
            return
        if which == "base":
            self.project["base"] = slot
            self.changed()
            self.show_props()
        else:
            self.set_prop("slot", slot)

    def pick_colour(self, button):
        layer = self.layer()
        if not layer:
            return
        popover = Gtk.Popover(relative_to=button, position=Gtk.PositionType.LEFT)
        picker = ColourPicker(self.core, lambda rgb: self.set_prop("rgb", rgb))
        picker.set_rgb(layer["rgb"])
        popover.add(picker)
        picker.show_all()
        popover.connect("closed", lambda _: self.show_props())
        popover.popup()

    def use_dropper(self, _):
        """Take the selected layer's colour from anywhere on screen."""
        def hover(rgb):
            self.status.set_markup(f"<span foreground='{GREY}'>Click to take </span>"
                                   f"<span foreground='{ORANGE}'>{self.core.hex_code(rgb)}</span>"
                                   f"<span foreground='{GREY}'>. Esc cancels.</span>")
        if self.layer() and self.core.pick_screen_colour(self.canvas, lambda rgb: self.set_prop("rgb", rgb), hover,
                                                         lambda picked: self.show_props() or self.show_status()):
            self.status.set_markup(f"<span foreground='{GREY}'>Click anywhere on screen to take its colour. Esc cancels.</span>")

    def add_popover(self, button):
        """The shape library: basic shapes, every game emblem and your uploaded images."""
        popover = Gtk.Popover(relative_to=button, position=Gtk.PositionType.LEFT)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        tabs = Gtk.Box(spacing=6)
        pages = Gtk.Stack()
        box.pack_start(tabs, False, False, 0)
        box.pack_start(pages, True, True, 0)

        def grid_of(keys, tooltips):
            grid = flowbox(6)
            for key, tip in zip(keys, tooltips):
                tile = Gtk.Image(tooltip_text=tip)
                self.ui.show_image(tile, shape_thumb(self.data, key, THUMB * self.ui.scale))
                grid.add(tile)
            grid.connect("child-activated", lambda g, child: (popover.popdown(), self.add_layer(keys[child.get_index()])))
            return grid

        pages.add_named(scrolled(grid_of(list(SHAPES), list(SHAPES)), 6 * (THUMB + 10), 250, self.ui), "shapes")
        emblem_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        categories = Gtk.ComboBoxText()
        for category in self.data.all_categories:
            categories.append(category, self.data.category_label(category))
        holder = Gtk.Box()
        emblem_box.pack_start(categories, False, False, 0)
        emblem_box.pack_start(holder, True, True, 0)

        def show_category(_):
            for child in holder.get_children():
                holder.remove(child)
            category = categories.get_active_id()
            files = self.data.all_categories.get(category, [])
            holder.pack_start(scrolled(grid_of([f"emblem:{category}/{p.stem}" for p in files], [p.stem for p in files]),
                                       6 * (THUMB + 10), 214, self.ui), True, True, 0)
            holder.show_all()
        categories.connect("changed", show_category)
        categories.set_active(0)
        pages.add_named(emblem_box, "emblems")
        uploads = sorted(self.core.load_manifest())
        pages.add_named(scrolled(grid_of([f"upload:{n}" for n in uploads], uploads), 6 * (THUMB + 10), 250, self.ui), "uploads")

        toggles = {}

        def show_page(page):
            for name, toggle in toggles.items():
                toggle.handler_block_by_func(on_toggle)
                toggle.set_active(name == page)
                toggle.handler_unblock_by_func(on_toggle)
            pages.set_visible_child_name(page)

        def on_toggle(toggle, page):
            show_page(page)
        for page, label in (("shapes", "Shapes"), ("emblems", "Game emblems"), ("uploads", "Your images")):
            toggle = self.ui.styled(Gtk.ToggleButton(label=label), "sw-btn", "sw-small", "sw-tab")
            toggle.connect("toggled", on_toggle, page)
            tabs.pack_start(toggle, True, True, 0)
            toggles[page] = toggle
        popover.add(box)
        box.show_all()
        show_page("shapes")
        popover.popup()

    # canvas
    def canvas_size(self):
        return self.canvas.get_allocated_width()

    def draw(self, area, cr):
        size, scale = self.canvas_size(), self.ui.scale
        art = render_project(self.data, self.project, size * scale, self.layer_colour)
        if self.kind == "emblem":  # show see-through areas against a dark checkerboard
            cell = 12
            for y in range(0, size, cell):
                for x in range(0, size, cell):
                    shade = 0.16 if (x // cell + y // cell) % 2 else 0.11
                    cr.set_source_rgb(shade, shade + 0.02, shade + 0.02)
                    cr.rectangle(x, y, cell, cell)
                    cr.fill()
        cr.set_source_surface(cairo_surface(art, scale), 0, 0)
        cr.paint()

        # Guides: where the game's hexagon cuts the art and where the emblem sits.
        if self.kind == "background":
            ox, oy, span = self.core.FLAG_BG
        else:
            ox, oy, span = self.core.symbol_box(self.canvas_options()[0])
        to_canvas = [((px - ox) / span * size, (py - oy) / span * size) for px, py in hexagon(*FLAG_HEX)]
        full, cut = self.canvas_options() if self.kind == "emblem" else (False, True)
        if cut:  # the hexagon cuts off everything outside it: shade that part
            cr.rectangle(0, 0, size, size)
            _polygon(cr, to_canvas)
            cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
            cr.set_source_rgba(0.03, 0.05, 0.05, 0.6)
            cr.fill()
            cr.set_fill_rule(cairo.FILL_RULE_WINDING)
        cr.set_line_width(1)
        cr.set_dash([4, 4])
        cr.set_source_rgba(1, 1, 1, 0.45)
        _polygon(cr, to_canvas)
        cr.stroke()
        if self.kind == "background":  # where the emblem sits on this background
            sx, sy, ss = ((v - o) / span * size for v, o in zip(self.core.FLAG_SYMBOL, (ox, oy, 0)))
            cr.set_source_rgba(*CYAN, 0.5)
            cr.rectangle(sx, sy, ss, ss)
            cr.stroke()
        layer = self.layer()
        if layer:
            cr.set_dash([5, 3])
            cr.set_source_rgba(*CYAN, 0.95)
            cr.save()
            cr.transform(layer_matrix(layer, size))
            cr.rectangle(-0.5, -0.5, 1, 1)
            cr.restore()
            cr.stroke()
        cr.set_dash([])
        cr.set_source_rgba(*CYAN, 0.45)
        cr.rectangle(0.5, 0.5, size - 1, size - 1)
        cr.stroke()
        return False

    def press(self, area, event):
        if event.button != 1:
            return False
        size, layers = self.canvas_size(), self.project["layers"]
        current = self.layer()
        if not (current and hit(current, event.x, event.y, size)):
            under = [i for i in reversed(range(len(layers))) if hit(layers[i], event.x, event.y, size)]
            if under:
                self.selected = under[0]
                self.fill_layers()
                self.canvas.queue_draw()
        layer = self.layer()
        self.drag = (event.x, event.y, layer["x"], layer["y"]) if layer and hit(layer, event.x, event.y, size) else None
        return True

    def motion(self, area, event):
        layer = self.layer()
        if self.drag and layer and event.state & Gdk.ModifierType.BUTTON1_MASK:
            x0, y0, lx, ly = self.drag
            size = self.canvas_size()
            layer["x"] = min(max(lx + (event.x - x0) / size, -0.25), 1.25)
            layer["y"] = min(max(ly + (event.y - y0) / size, -0.25), 1.25)
            self.changed()
        return True

    def scroll(self, area, event):
        layer = self.layer()
        if not layer:
            return False
        if event.direction == Gdk.ScrollDirection.SMOOTH:
            steps = -event.get_scroll_deltas()[2]
        else:
            steps = {Gdk.ScrollDirection.UP: 1, Gdk.ScrollDirection.DOWN: -1}.get(event.direction, 0)
        if event.state & Gdk.ModifierType.SHIFT_MASK:
            layer["rot"] = (layer["rot"] + 5 * steps + 180) % 360 - 180
        elif event.state & Gdk.ModifierType.CONTROL_MASK:
            layer["stretch"] = min(max(layer["stretch"] + 0.05 * steps, -2), 2)
        else:
            layer["size"] = min(max(layer["size"] * 1.06 ** steps, 0.02), 1.6)
        self.show_props()
        self.changed()
        return True


class MakerPage:
    """Background and Emblem editors, switched by two toggles."""

    def __init__(self, ui, data):
        self.widget = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        modes = Gtk.Box(spacing=8)
        self.stack = Gtk.Stack()
        self.editors = {kind: Editor(ui, data, kind) for kind in ("background", "emblem")}
        self.toggles = {}
        for kind, label in (("background", "Background"), ("emblem", "Emblem")):
            self.stack.add_named(self.editors[kind].widget, kind)
            toggle = ui.styled(Gtk.ToggleButton(label=label), "sw-btn", "sw-small", "sw-tab")
            toggle.connect("toggled", self.toggled, kind)
            modes.pack_start(toggle, False, False, 0)
            self.toggles[kind] = toggle
        self.widget.pack_start(modes, False, False, 0)
        self.widget.pack_start(self.stack, True, True, 0)
        self.busy = False
        self.current = "background"
        self.show("background")

    def show(self, kind):
        self.current = kind  # the stack can't report its visible page until the window is shown
        self.busy = True
        for name, toggle in self.toggles.items():
            toggle.set_active(name == kind)
        self.busy = False
        self.stack.set_visible_child_name(kind)
        self.refresh()

    def toggled(self, toggle, kind):
        if not self.busy:
            self.show(kind)

    def refresh(self):
        """Picks up colour and emblem changes from the other tabs."""
        editor = self.editors[self.current]
        editor.update_preview()
        editor.fill_layers()
        editor.canvas.queue_draw()
