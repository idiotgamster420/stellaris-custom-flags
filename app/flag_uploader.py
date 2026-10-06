#!/usr/bin/python3
"""Stellaris Flag Uploader.

Creates and manages the "Custom Flags" mod in your Stellaris mod folder (set
CUSTOM_FLAGS_MOD to use another folder), built partly from your installed game.

Adds your own images to the "Custom" emblem category in the flag editor.
Each upload becomes its own emblem; pick one to use it as your flag, or pick
any other emblem to go back to a normal flag. The Colours tab adds your own
exact colours to the game's flag, map and ship colour pickers.

  flag_uploader.py                       open the window
  flag_uploader.py --upload A.png B.jpg  add images (--fit to pad instead of crop,
                                         --colour-map to keep colours on the galaxy map)
  flag_uploader.py --list                list uploaded emblems
  flag_uploader.py --remove NAME         remove one
  flag_uploader.py --check               show where it finds the game and the mod

Originals are kept in uploads/ so Fill/Fit and the map style can be changed later. The window
borrows its look (textures, fonts, flag shape) from your Stellaris install.
"""
import argparse
import colorsys
import ctypes
import ctypes.util
import functools
import json
import math
import os
import re
import string
import struct
import subprocess
import sys
import types
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageOps

APP = Path(__file__).resolve().parent
MOD_NAME = "custom_flags"


def windows_documents():
    import ctypes.wintypes
    path = ctypes.create_unicode_buffer(ctypes.wintypes.MAX_PATH)
    ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, path)  # CSIDL_PERSONAL: Documents, even when moved
    return Path(path.value)


def stellaris_user_dir():
    """Where Stellaris keeps this user's mods and saves."""
    if sys.platform == "win32":
        return windows_documents() / "Paradox Interactive" / "Stellaris"
    candidates = [Path.home() / ".local/share/Paradox Interactive/Stellaris",
                  Path.home() / ".var/app/com.valvesoftware.Steam/.local/share/Paradox Interactive/Stellaris"]
    return next((c for c in candidates if c.exists()), candidates[0])


def find_mod_dir():
    if os.environ.get("CUSTOM_FLAGS_MOD"):
        return Path(os.environ["CUSTOM_FLAGS_MOD"]).resolve()
    if (APP / "descriptor.mod").exists():  # older installs run from inside the mod folder
        return APP
    return stellaris_user_dir() / "mod" / MOD_NAME


MOD = find_mod_dir()
OUT = MOD / "flags" / "custom_flags"
SIZES = {OUT: 128, OUT / "map": 256, OUT / "small": 24}
UPLOADS = MOD / "uploads"
MANIFEST = UPLOADS / "manifest.json"
CACHE = (Path(os.environ["LOCALAPPDATA"]) if sys.platform == "win32" and os.environ.get("LOCALAPPDATA")
         else Path.home() / ".cache") / "stellaris-flag-uploader"
COLOURS = MOD / "colours.json"
COLOURS_TXT = MOD / "flags" / "colors.txt"
COLOURS_LOC = MOD / "localisation" / "english" / "custom_flags_colours_l_english.yml"
COLOURS_GUI = MOD / "interface" / "zz_custom_flags_colours.gui"
# The game's colour grids hold exactly the 72 vanilla colours. Removing the gap between
# swatches fits a 9x10 grid in the same space: (gui file, window, grid, slot size, per row).
COLOUR_GRIDS = [
    ("customize_species_editors.gui", "empire_flag_editor", "colors", 20, 9),
    ("customize_species_shipsets.gui", "ship_gfx_culture_browser_editor", "ship_colors", 15, 9),
]
MAX_COLOURS = 90 - 72
EXTS = ["png", "jpg", "jpeg", "webp", "bmp", "gif", "tga", "tif", "tiff"]

# GFX_empire_flag_200 in interface/game_setup/customization.gfx: a 212px
# hexagon sprite with the emblem drawn in a 130px square at (41, 41).
FLAG_SPRITE, FLAG_SYMBOL = 212, (41, 41, 130)
FLAG_BG = (13, 13, 186)  # where the background is drawn in that sprite
# "Fill the whole flag" emblems: their canvas is 1.5x the emblem square, enough to cover
# the hexagon. The flag shader (written by write_flag_shader) recognises them by a
# magenta pixel at alpha 1/255 in their top-left corner, invisible in game.
FULL_SCALE = 1.5
MARKER = (255, 0, 255, 1)
SHADER = MOD / "gfx" / "FX" / "flag_sprite.shader"
PREVIEW_BG = (62, 62, 62)  # the "dark_grey" flag colour
PREVIEW_SIZE = 260
COLOUR_PREVIEW_SIZE = 160
PICKER_WIDTH, PICKER_HEIGHT, HUE_BAR_HEIGHT = 290, 160, 22
THUMB_SIZE = 52
MAP_PREVIEW_SIZE = 56
MAP_SPACE = (9, 13, 20)  # roughly the galaxy map's dark space
# Text colours from interface/fonts.gfx.
YELLOW, RED, GREY = "#f7fc34", "#fc5646", "#c6c6c6"


def dds_bytes(levels):
    """Uncompressed 32-bit BGRA DDS (same pixel format as vanilla emblems)."""
    w, h = levels[0].size
    flags = 0x1 | 0x2 | 0x4 | 0x8 | 0x1000  # caps, height, width, pitch, pixelformat
    caps = 0x1000  # texture
    mips = 0
    if len(levels) > 1:
        flags |= 0x20000  # mipmapcount
        caps |= 0x8 | 0x400000  # complex, mipmap
        mips = len(levels)
    header = struct.pack(
        "<4s7I44x8I5I",
        b"DDS ", 124, flags, h, w, w * 4, 0, mips,
        32, 0x41, 0, 32, 0xFF0000, 0xFF00, 0xFF, 0xFF000000,  # RGB | ALPHAPIXELS
        caps, 0, 0, 0, 0,
    )
    return header + b"".join(im.tobytes("raw", "BGRA") for im in levels)


def open_image(path):
    """Image.open, but fast for uncompressed DDS (the game's flags and UI textures):
    Pillow decodes those in pure Python, about 0.3 s for one flag background."""
    path = Path(path)
    if path.suffix.lower() == ".dds":
        data = path.read_bytes()
        if data[:4] == b"DDS " and len(data) >= 128:
            height, width = struct.unpack_from("<II", data, 12)
            flags, bits = struct.unpack_from("<I", data, 80)[0], struct.unpack_from("<I", data, 88)[0]
            masks = struct.unpack_from("<4I", data, 92)
            if not flags & 0x1:  # no alpha
                masks = masks[:3] + (0,)
            layout = {(32, (0xFF0000, 0xFF00, 0xFF, 0xFF000000)): ("RGBA", "BGRA"),
                      (32, (0xFF0000, 0xFF00, 0xFF, 0)): ("RGB", "BGRX"),
                      (24, (0xFF0000, 0xFF00, 0xFF, 0)): ("RGB", "BGR"),
                      (32, (0xFF, 0xFF00, 0xFF0000, 0xFF000000)): ("RGBA", "RGBA")}.get((bits, masks))
            size = width * height * bits // 8
            if layout and not flags & 0x4 and len(data) >= 128 + size:  # 0x4: compressed, Pillow is fast for those
                return Image.frombytes(layout[0], (width, height), data[128:128 + size], "raw", layout[1])
    return Image.open(path)


def square(img, size, fit):
    if not fit:
        return ImageOps.fit(img, (size, size), Image.LANCZOS)
    img = ImageOps.contain(img, (size, size), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(img, ((size - img.width) // 2, (size - img.height) // 2))
    return canvas


def mip_chain(img):
    levels = [img]
    while levels[-1].width > 1:
        s = levels[-1].width // 2
        levels.append(levels[-1].resize((s, s), Image.LANCZOS))
    return levels


def load_manifest():
    try:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_manifest(manifest):
    UPLOADS.mkdir(exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8")


def source(name):
    return UPLOADS / f"{name}.png"


def has_transparency(img):
    """True if the picture itself has see-through areas (a logo rather than a photo)."""
    alpha = img.getchannel("A")
    return sum(alpha.histogram()[:250]) > 0.02 * img.width * img.height


def map_emblem(sq, white):
    """The galaxy map draws emblems in their own colours at 65% opacity over dark space
    (PixelShaderCentroid in gfx/FX/border.shader), so vanilla map emblems are white
    silhouettes. With `white`, do the same; otherwise keep the picture's colours."""
    if not white:
        return sq
    white = Image.new("RGBA", sq.size, (255, 255, 255, 0))
    white.putalpha(sq.getchannel("A"))
    return white


def symbol_box(full):
    """The emblem's canvas in the 212px flag sprite: (x, y, size)."""
    x, y, s = FLAG_SYMBOL
    if full:
        grow = s * (FULL_SCALE - 1) / 2
        return x - grow, y - grow, s * FULL_SCALE
    return x, y, s


def hex_cut(art, full, game):
    """Cut emblem art to the flag's hexagon, as it sits on the flag."""
    if not game:
        return art
    mask = open_image(game / "gfx/interface/flags/empire_flag_200_mask.dds").convert("RGBA").getchannel("A")
    x, y, s = symbol_box(full)
    region = mask.crop((round(x), round(y), round(x + s), round(y + s))).resize(art.size, Image.LANCZOS)
    cut = art.copy()
    cut.putalpha(ImageChops.multiply(art.getchannel("A"), region))
    return cut


def emblem_art(img, size, fit, full=False, cut=True, game=None):
    """An upload as emblem art: squared, and for full-flag emblems maybe cut to the hexagon.
    (Ordinary emblems are cut by the game itself.)"""
    art = square(img, size, fit)
    return hex_cut(art, True, game) if full and cut else art


def render(name, fit, white_map, full=False, cut=True):
    img = Image.open(source(name)).convert("RGBA")
    game = find_game() if full and cut else None
    for folder, size in SIZES.items():
        folder.mkdir(parents=True, exist_ok=True)
        art = emblem_art(img, size, fit, full, cut, game)
        if folder.name == "map":  # the galaxy map draws it without the flag shader
            levels = mip_chain(map_emblem(art, white_map))
        else:
            if full:
                block = max(1, -(-size // 24))  # at least one pixel even in the 24px icon
                art.paste(MARKER, (0, 0, block, block))
            levels = [art]
        (folder / f"{name}.dds").write_bytes(dds_bytes(levels))


def settings(info):
    """An upload's settings with defaults for ones saved before a setting existed."""
    return {"fit": info.get("fit", False), "white_map": info.get("white_map", True),
            "full": info.get("full", False), "cut": info.get("cut", True)}


def upload(path, fit=False, white_map=None):
    """Add an image as a new emblem and return its name. By default the galaxy map
    version is white for logos (pictures with see-through areas), coloured for photos."""
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGBA")
    manifest = load_manifest()
    base = re.sub(r"[^a-z0-9]+", "_", Path(path).stem.lower()).strip("_") or "flag"
    name, n = base, 2
    while name in manifest:
        name, n = f"{base}_{n}", n + 1
    UPLOADS.mkdir(exist_ok=True)
    img.save(source(name))
    if white_map is None:
        white_map = has_transparency(img)
    render(name, fit, white_map)
    manifest[name] = {"fit": fit, "white_map": white_map, "full": False, "cut": True}
    save_manifest(manifest)
    return name


def update(name, **changes):
    """Change an upload's settings (fit, white_map, full, cut) and re-render it."""
    manifest = load_manifest()
    manifest[name].update(changes)
    save_manifest(manifest)
    render(name, **settings(manifest[name]))
    if "full" in changes:
        write_flag_shader(find_game())


def remove(name):
    manifest = load_manifest()
    manifest.pop(name, None)
    save_manifest(manifest)
    source(name).unlink(missing_ok=True)
    for folder in SIZES:
        (folder / f"{name}.dds").unlink(missing_ok=True)
    write_flag_shader(find_game())


FULL_SYMBOL_HLSL = """
		// Custom Flags mod: "Fill the whole flag" emblems carry a magenta pixel at alpha 1/255
		// in their top-left corner. They use a canvas 1.5x the emblem square, aren't cut to
		// the flag's shape, and draw over the frame's border but under its gloss.
		float FullFlagSymbol()
		{
			float4 vMark = tex2Dlod( SymbolTexture, float4( 0.01f, 0.01f, 0.f, 0.f ) );
			return step( 0.9f, vMark.r ) * step( vMark.g, 0.1f ) * step( 0.9f, vMark.b ) * step( vMark.a, 0.02f ) * step( 0.001f, vMark.a );
		}

		float4 AddFullSymbol( float4 vColor, float2 vUV, float2 vFrameUV )
		{
			if( FullFlagSymbol() < 0.5f )
				return vColor;
			float2 vSymbolUV = ( vUV - ( SymbolPos - SymbolSize * 0.25f ) ) / ( SymbolSize * 1.5f );
			if( vSymbolUV.x < 0.f || vSymbolUV.x > 1.f || vSymbolUV.y < 0.f || vSymbolUV.y > 1.f )
				return vColor;
			float4 vSymbol = tex2D( SymbolTexture, vSymbolUV );
			float4 vFrame = tex2D( FrameTexture, vFrameUV ) * ModulateColor;
			float vInside = tex2D( MaskingTexture, ( vUV - MaskOffset ) / MaskSize ).a;
			float vGloss = step( 0.99f, vInside ) * ( 1.f - smoothstep( 0.255f, 0.27f, vFrame.a ) );
			float vAlpha = vSymbol.a + vColor.a * ( 1.f - vSymbol.a );
			vColor.rgb = ( vSymbol.rgb * vSymbol.a + vColor.rgb * vColor.a * ( 1.f - vSymbol.a ) ) / max( vAlpha, 0.001f );
			vColor.a = vAlpha;
			vColor.rgb = lerp( vColor.rgb, vFrame.rgb, vFrame.a * vGloss * vSymbol.a );
			return vColor;
		}

"""


def write_flag_shader(game):
    """While any emblem uses "Fill the whole flag", install a copy of the game's flag shader
    that draws them (gfx/FX/flag_sprite.shader); otherwise remove it so the game's own is
    used. Built from the installed game, so it follows game updates. Other emblems take
    the game's original code path either way."""
    if not game or not any(settings(info)["full"] for info in load_manifest().values()):
        SHADER.unlink(missing_ok=True)
        return
    text = (game / "gfx/FX/flag_sprite.shader").read_text(encoding="utf-8")
    anchors = {
        "\t\tfloat4 GetFlagColor( float2 vUV )": FULL_SYMBOL_HLSL + "\t\tfloat4 GetFlagColor( float2 vUV )",
        "if( vSymbolUV.x >= 0.f &&": "if( FullFlagSymbol() < 0.5f && vSymbolUV.x >= 0.f &&",
    }
    for old, new in anchors.items():
        if text.count(old) != 1:
            raise ValueError("the game's flag shader has changed; full-flag emblems can't be drawn")
        text = text.replace(old, new)
    text, n = re.subn(r"^([ \t]*)vColor = AddFrame\( vColor, v\.vTexCoord \);",
                      r"\g<0>\n\1vColor = AddFullSymbol( vColor, v.vFullTexCoord, v.vTexCoord );", text, flags=re.M)
    if n != 4:
        raise ValueError("the game's flag shader has changed; full-flag emblems can't be drawn")
    SHADER.parent.mkdir(parents=True, exist_ok=True)
    SHADER.write_text(text, encoding="utf-8")


def game_running():
    try:
        if sys.platform == "win32":
            tasks = subprocess.run(["tasklist", "/FI", "IMAGENAME eq stellaris.exe", "/NH"], capture_output=True,
                                   text=True, creationflags=0x08000000).stdout  # CREATE_NO_WINDOW
            return "stellaris.exe" in tasks.lower()
        return subprocess.run(["pgrep", "-x", "stellaris"], capture_output=True).returncode == 0
    except OSError:
        return False


def pick_screen_colour(widget, on_pick, on_hover=None, on_done=None):
    """Eyedropper: the next left click anywhere on screen picks the colour under the
    pointer (emblems in this app, a browser, the game in a window...). Right click or
    Esc cancels. Reads the screen through X11. Returns False if it couldn't start."""
    from gi.repository import Gdk
    toplevel = widget.get_toplevel()
    window = toplevel.get_window()
    display = window.get_display()
    seat = display.get_default_seat()
    root = Gdk.get_default_root_window()
    handlers = []

    def colour_at(event):
        unshaded = flat_colour_at(toplevel, event.x_root, event.y_root)
        if unshaded:
            return unshaded
        pixels = Gdk.pixbuf_get_from_window(root, int(event.x_root), int(event.y_root), 1, 1)
        return list(pixels.get_pixels()[:3]) if pixels else None

    def finish(rgb=None):
        seat.ungrab()
        for handler in handlers:
            toplevel.disconnect(handler)
        if rgb:
            on_pick(rgb)
        if on_done:
            on_done(bool(rgb))

    def press(_, event):
        finish(colour_at(event) if event.button == 1 else None)
        return True

    def motion(_, event):
        rgb = colour_at(event) if on_hover else None
        if rgb:
            on_hover(rgb)
        return True

    def key(_, event):
        if event.keyval == Gdk.KEY_Escape:
            finish()
        return True

    toplevel.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.KEY_PRESS_MASK)
    handlers += [toplevel.connect("button-press-event", press), toplevel.connect("motion-notify-event", motion),
                 toplevel.connect("key-press-event", key)]
    status = seat.grab(window, Gdk.SeatCapabilities.ALL, False, Gdk.Cursor.new_from_name(display, "crosshair"), None, None, None)
    if status != Gdk.GrabStatus.SUCCESS:
        for handler in handlers:
            toplevel.disconnect(handler)
        return False
    return True


def flat_colour_at(toplevel, x_root, y_root):
    """On one of this app's flag previews, the colour before the game's frame shading
    (a 25% white-to-black gloss over the whole flag), so the eyedropper gets true colours."""
    from gi.repository import Gtk
    ox, oy = toplevel.get_window().get_origin()[1:]
    widgets = [toplevel]
    while widgets:
        widget = widgets.pop()
        if isinstance(widget, Gtk.Container):
            widgets.extend(widget.get_children())
        flat = getattr(widget, "flat_image", None)
        if flat is None or not widget.get_mapped():
            continue
        wx, wy = widget.translate_coordinates(toplevel, 0, 0)
        scale, allocation = widget.get_scale_factor(), widget.get_allocation()
        w, h = flat.width / scale, flat.height / scale
        x = x_root - ox - wx - (allocation.width - w) / 2  # Gtk.Image centres its picture
        y = y_root - oy - wy - (allocation.height - h) / 2
        if 0 <= x < w and 0 <= y < h:
            r, g, b, a = flat.getpixel((int(x * scale), int(y * scale)))
            return [r, g, b] if a >= 128 else None
    return None


def dropper_button(styled):
    from gi.repository import Gtk
    button = styled(Gtk.Button(tooltip_text="Eyedropper: click anywhere on screen to take its colour (Esc cancels)"),
                    "sw-btn", "sw-small", "sw-dropper")
    if Gtk.IconTheme.get_default().has_icon("color-select-symbolic"):
        button.add(Gtk.Image.new_from_icon_name("color-select-symbolic", Gtk.IconSize.BUTTON))
    else:
        button.set_label("Pick")
    return button


def steam_roots():
    if sys.platform == "win32":
        roots = []
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
                roots.append(Path(winreg.QueryValueEx(key, "SteamPath")[0]))
        except OSError:
            pass
        return roots + [Path(r"C:\Program Files (x86)\Steam")]
    home = Path.home()
    return [home / ".local/share/Steam", home / ".steam/steam", home / ".var/app/com.valvesoftware.Steam/.local/share/Steam"]


def find_game():
    roots = steam_roots()
    libraries = list(roots)
    for root in roots:
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            libraries += [Path(p.replace("\\\\", "\\")) for p in re.findall(r'"path"\s+"([^"]+)"', vdf.read_text(encoding="utf-8", errors="replace"))]
    for library in libraries:
        game = library / "steamapps" / "common" / "Stellaris"
        if (game / "gfx" / "interface").is_dir():
            return game
    return None


# --- the mod's own files, created and refreshed by the app (never your uploads, colours or designs)

USAGE = "random = no\nshow_in_designer = yes\n"
CATEGORY_LOC = '\ufeffl_english:\n FLAG_CATEGORY_custom_flags:0 "Custom"\n'
SLOT_GFX = """spriteTypes = {
	# Like GFX_flag_no_mask, but drawn with a see-through background
	# (gfx/FX/custom_flags_emblem_slot.shader) so the slot tile shows behind emblems.
	flagSpriteType = {
		name = "GFX_custom_flags_emblem_slot"
		textureFile = "gfx/interface/flags/flag_no_frame.dds"		#this one will determine the size of the sprite
		masking_texture = "gfx/interface/flags/flag_full_mask.dds"
		effectFile = "gfx/FX/custom_flags_emblem_slot.shader"
	}

	# Mid-tone hex tile behind each emblem in the flag editor, so black emblems stay visible.
	spriteType = {
		name = "GFX_custom_flags_emblem_slot_bg"
		textureFile = "gfx/interface/custom_flags/emblem_slot_bg.dds"
	}
}
"""
MOD_README = """Custom Flags
============

Made and managed by the Stellaris Flag Uploader app: open it to upload emblems,
make colours, backgrounds and emblems, and plan flags. Enable "Custom Flags" in
your playset in the Paradox launcher, then find your emblems under "Custom" in
the flag editor. Restart Stellaris after changing anything.

Your pictures are kept in uploads/, your designs in maker/ and colours.json.
"""


def write_if_changed(path, data):
    path = Path(path)
    data = data.encode("utf-8") if isinstance(data, str) else data
    if not path.exists() or path.read_bytes() != data:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def descriptor(game):
    """The mod's descriptor, marked for the installed game's version."""
    version = "v4.*"
    try:
        raw = json.loads((game / "launcher-settings.json").read_text(encoding="utf-8"))["rawVersion"]  # e.g. "v4.5.2"
        version = re.sub(r"^(v\d+\.\d+).*", r"\1.*", raw)
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return f'version="1.0"\ntags={{\n\t"Graphics"\n}}\nname="Custom Flags"\nsupported_version="{version}"\n'


def emblem_slot_bg(size=100, top=(84, 120, 111), bottom=(50, 76, 70), line=(175, 230, 216, 115), edge=(120, 220, 200, 150), r=9):
    """Mid-tone teal tile with a faint hex grid, so black, white and coloured emblems all stand out."""
    tile = Image.new("RGBA", (size, size))
    draw = ImageDraw.Draw(tile)
    for y in range(size):  # lighter at the top, like the game's panels
        t = y / (size - 1)
        draw.line([(0, y), (size, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,))
    grid = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    lines = ImageDraw.Draw(grid)
    w, h = math.sqrt(3) * r, 1.5 * r  # pointy-top hexagons, like the flag
    for row in range(-1, int(size / h) + 2):
        for col in range(-1, int(size / w) + 2):
            cx, cy = col * w + (w / 2 if row % 2 else 0), row * h
            lines.polygon([(cx + r * math.cos(math.radians(60 * k - 30)), cy + r * math.sin(math.radians(60 * k - 30)))
                           for k in range(6)], outline=line)
    tile.alpha_composite(grid)
    ImageDraw.Draw(tile).rectangle([0, 0, size - 1, size - 1], outline=edge)
    return tile


def slot_files(game):
    """The emblem picker's slot (interface override and its shader), from the game's own
    flag_texture_entry and flag shader: the same, but drawn on a tile instead of black."""
    shader = (game / "gfx/FX/flag_sprite.shader").read_text(encoding="utf-8")
    for old, new in (
            ("\t\t\tfloat4 vColor = float4( 0, 0, 0, 1 );\n",
             "\t\t\t// Custom Flags: start see-through instead of black when no background colours are set\n"
             "\t\t\t// (emblem slots), so the slot tile shows behind the emblem. Pattern slots stay opaque.\n"
             "\t\t\tfloat3 vAnyColor = BackgroundColor[0].rgb + BackgroundColor[1].rgb + BackgroundColor[2].rgb;\n"
             "\t\t\tfloat4 vColor = float4( 0, 0, 0, saturate( dot( vAnyColor, float3( 1, 1, 1 ) ) * 1000.f ) );\n"),
            ("vColor.rgb = lerp( vColor.rgb, vSymbol.rgb, vSymbol.a );",
             "vColor = lerp( vColor, float4( vSymbol.rgb, 1.f ), vSymbol.a );"),
            ("vColor.rgb = lerp( vColor.rgb * vColor.a, FrameColor.rgb, FrameColor.a );",
             "vColor.rgb = lerp( vColor.rgb, FrameColor.rgb, FrameColor.a );  // Custom Flags: not premultiplied")):
        if shader.count(old) != 1:
            raise ValueError("the game's flag shader has changed; the emblem picker keeps its black slots")
        shader = shader.replace(old, new)
    entry = gui_block((game / "interface/customize_species_editors.gui").read_text(encoding="utf-8"),
                      "containerWindowType", "flag_texture_entry")
    tile = ('iconType = {\n\t\t\tname = "custom_flags_slot_bg"\n\t\t\tspriteType = "GFX_custom_flags_emblem_slot_bg"\n'
            '\t\t\tscale = 0.5\n\t\t\talwaystransparent = yes\n\t\t}\n\n\t\tbuttonType = {')
    if entry.count('"GFX_flag_no_mask"') != 1 or "buttonType = {" not in entry:
        raise ValueError("the game's emblem picker has changed; it keeps its black slots")
    entry = entry.replace('"GFX_flag_no_mask"', '"GFX_custom_flags_emblem_slot"').replace("buttonType = {", tile, 1)
    gui = ("# Generated by the Stellaris Flag Uploader from your game's flag_texture_entry\n"
           "# (customize_species_editors.gui); loaded after it, so this one is used.\n"
           "guiTypes = {\n\t" + entry + "\n}\n")
    return shader, gui


def ensure_mod(game):
    """Create or refresh the mod's own files and its entry for the Paradox launcher."""
    write_if_changed(MOD / "descriptor.mod", descriptor(game))
    write_if_changed(MOD.parent / f"{MOD_NAME}.mod", descriptor(game) + f'path="{MOD.as_posix()}"\n')
    write_if_changed(OUT / "usage.txt", USAGE)
    write_if_changed(MOD / "localisation/english/custom_flags_l_english.yml", CATEGORY_LOC)
    write_if_changed(MOD / "interface/custom_flags.gfx", SLOT_GFX)
    write_if_changed(MOD / "gfx/interface/custom_flags/emblem_slot_bg.dds", dds_bytes([emblem_slot_bg()]))
    write_if_changed(MOD / "README.txt", MOD_README)
    UPLOADS.mkdir(parents=True, exist_ok=True)
    if game:
        shader, gui = slot_files(game)
        write_if_changed(MOD / "gfx/FX/custom_flags_emblem_slot.shader", shader)
        write_if_changed(MOD / "interface/zz_custom_flags.gui", gui)


def install_desktop_entry():
    """When run as an AppImage, add (or update) its entry in the app menu."""
    appimage = os.environ.get("APPIMAGE")
    if not sys.platform.startswith("linux") or not appimage or not (APP / "icon.png").exists():
        return
    data = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    icon = data / "icons/hicolor/256x256/apps/stellaris-flag-uploader.png"
    write_if_changed(icon, (APP / "icon.png").read_bytes())
    write_if_changed(data / "applications/stellaris-flag-uploader.desktop",
                     "[Desktop Entry]\nType=Application\nName=Stellaris Flag Uploader\n"
                     "Comment=Custom flags, colours and emblems for Stellaris\n"
                     f'Exec="{appimage}"\nIcon={icon}\nTerminal=false\nCategories=Game;\n')


def hsv_to_rgb(h, s, v):
    """Hue 0-360, saturation and brightness 0-100."""
    return tuple(round(c * 255) for c in colorsys.hsv_to_rgb((h % 360) / 360, s / 100, v / 100))


def rgb_to_hsv(rgb):
    h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    return [h * 360, s * 100, v * 100]


def parse_colour_code(text):
    """#RRGGBB or rgb(r, g, b) (also bare "r, g, b"), as copied from colour picker sites."""
    text = text.strip()
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", text)
    if m:
        return [int(m.group(1)[i:i + 2], 16) for i in (0, 2, 4)]
    m = re.fullmatch(r"(?:rgba?\(\s*)?(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,[^)]*)?\)?", text)
    if m and all(int(v) <= 255 for v in m.groups()):
        return [int(v) for v in m.groups()]
    return None


def hex_code(rgb):
    return "#%02X%02X%02X" % tuple(rgb)


def load_colours():
    """Your colours, in the order they appear in the game: [{key, name, rgb}]."""
    try:
        return json.loads(COLOURS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def save_colours(colours):
    COLOURS.write_text(json.dumps(colours, indent=1), encoding="utf-8")


def new_colour_key(colours):
    """Keys are what saves store, so they stay fixed when a colour is renamed."""
    used = {c["key"] for c in colours}
    n = 1
    while f"cf_custom_{n}" in used:
        n += 1
    return f"cf_custom_{n}"


def gui_block(text, kind, name):
    """The whole `kind = { name = "<name>" ... }` block from a .gui file."""
    m = re.search(rf'{kind}\s*=\s*\{{\s*name\s*=\s*"{name}"', text)
    if not m:
        raise ValueError(f"{name} not found")
    depth = 0
    for j in range(text.index("{", m.start()), len(text)):
        depth += {"{": 1, "}": -1}.get(text[j], 0)
        if depth == 0:
            return text[m.start():j + 1]
    raise ValueError(f"{name} is not closed")


def widen_grid(window, grid, slot, per_row):
    old = gui_block(window, "gridBoxType", grid)
    new, n1 = re.subn(r"slotSize\s*=\s*\{[^}]*\}", f"slotSize = {{ width = {slot} height = {slot} }}", old)
    new, n2 = re.subn(r"max_slots_horizontal\s*=\s*\d+", f"max_slots_horizontal = {per_row}", new)
    if (n1, n2) != (1, 1):
        raise ValueError(f"the {grid} grid has an unexpected layout")
    return window.replace(old, new)


def write_colour_files(game):
    """Add your colours to the game: flags/colors.txt (vanilla's list with yours first),
    their names, and copies of the two colour-picker windows with room for them. Built
    from the installed game each time, so it follows game updates. No colours: vanilla."""
    colours = load_colours()
    if not colours:
        for path in (COLOURS_TXT, COLOURS_LOC, COLOURS_GUI):
            path.unlink(missing_ok=True)
        return
    if not game:
        raise FileNotFoundError("Stellaris isn't installed where Steam keeps it")

    swatches = "".join(
        f'\t{c["key"]} = {{ flag = rgb {{ {r} {g} {b} }} map = rgb {{ {r} {g} {b} }} ship = rgb {{ {r} {g} {b} }} }}\n'
        for c in colours for r, g, b in [c["rgb"]])
    vanilla = (game / "flags" / "colors.txt").read_text(encoding="utf-8")
    text, found = re.subn(r"^(colors\s*=\s*\{[^\n]*\n)", lambda m: m.group(1) + swatches, vanilla, count=1, flags=re.M)
    if not found:
        raise ValueError("the colour list in flags/colors.txt has an unexpected layout")

    def loc_name(c):
        return re.sub(r'[\"$§\\\n]', "", c["name"]).strip() or "Custom Colour"
    loc = "\ufeffl_english:\n" + "".join(
        f' FLAG_COLOR_{c["key"]}: "{loc_name(c)}\\n§g{hex_code(c["rgb"])}§!"\n' for c in colours)

    variables, windows = {}, []
    for file, window, grid, slot, per_row in COLOUR_GRIDS:
        source_gui = (game / "interface" / file).read_text(encoding="utf-8")
        block = gui_block(source_gui, "containerWindowType", window)
        for var in set(re.findall(r"@\w+", block)):
            value = re.search(rf"^{var}\s*=\s*(\S+)", source_gui, re.M)
            if not value:
                raise ValueError(f"{var} isn't defined in {file}")
            variables[var] = value.group(1)
        windows.append(widen_grid(block, grid, slot, per_row))
    gui = ("# Generated by flag_uploader.py from your Stellaris install; changes here are overwritten.\n"
           "# Copies of the flag editor and ship appearance windows with gap-free colour grids,\n"
           f"# which fit 90 colours (72 vanilla + up to {MAX_COLOURS} of yours) instead of 72.\n"
           + "".join(f"{k} = {v}\n" for k, v in sorted(variables.items()))
           + "\nguiTypes = {\n" + "".join(f"\t{w}\n\n" for w in windows) + "}\n")

    COLOURS_TXT.write_text(text, encoding="utf-8")
    COLOURS_LOC.parent.mkdir(parents=True, exist_ok=True)
    COLOURS_LOC.write_text(loc, encoding="utf-8")
    COLOURS_GUI.parent.mkdir(parents=True, exist_ok=True)
    COLOURS_GUI.write_text(gui, encoding="utf-8")


@functools.lru_cache(maxsize=8)
def flag_parts(game, size):
    """The game's hexagon flag frame and mask, and the frame's gloss alone (the flat 25%
    layer inside the hexagon, without the metal border), scaled to size."""
    flags = game / "gfx" / "interface" / "flags"
    frame = open_image(flags / "empire_flag_200_frame.dds").convert("RGBA")
    mask = open_image(flags / "empire_flag_200_mask.dds").convert("RGBA").getchannel("A")
    inside = mask.point(lambda v: 255 if v == 255 else 0)
    gloss = frame.copy()
    gloss.putalpha(ImageChops.multiply(frame.getchannel("A").point(lambda a: a if a <= 66 else 0), inside))
    return tuple(im.resize((size, size), Image.LANCZOS) for im in (frame, mask, gloss))


def place_clamped(texture, x, y, w, h, size):
    """Draw texture into (x, y, w, h) of a size x size canvas, repeating its edge pixels
    outward like the shader's clamped texture sampling."""
    t = texture.resize((w, h), Image.LANCZOS)
    out = Image.new(t.mode, (size, size))
    right, bottom = size - x - w, size - y - h
    if y > 0:
        out.paste(t.crop((0, 0, w, 1)).resize((w, y)), (x, 0))
    if bottom > 0:
        out.paste(t.crop((0, h - 1, w, h)).resize((w, bottom)), (x, y + h))
    if x > 0:
        out.paste(t.crop((0, 0, 1, h)).resize((x, h)), (0, y))
    if right > 0:
        out.paste(t.crop((w - 1, 0, w, h)).resize((right, h)), (x + w, y))
    for cx, cy, px, py, cw, ch in ((0, 0, 0, 0, x, y), (w - 1, 0, x + w, 0, right, y),
                                   (0, h - 1, 0, y + h, x, bottom), (w - 1, h - 1, x + w, y + h, right, bottom)):
        if cw > 0 and ch > 0:
            out.paste(t.getpixel((cx, cy)), (px, py, px + cw, py + ch))
    out.paste(t, (x, y))
    return out


def compose_flag(game, background, emblem, size, full=False):
    """A flag as the game's flag editor draws it (GFX_empire_flag_200): the background
    (RGB, already coloured), then the emblem, cut to the hexagon and framed. Full-flag
    emblems follow write_flag_shader: bigger canvas, not cut, over the border, under the gloss."""
    k = size / FLAG_SPRITE
    bx, by, bs = (round(v * k) for v in FLAG_BG)
    flag = place_clamped(background.convert("RGB"), bx, by, bs, bs, size).convert("RGBA")
    layer = Image.new("RGBA", (size, size))
    if emblem is not None:
        x, y, s = (round(v * k) for v in symbol_box(full))
        layer.paste(emblem.convert("RGBA").resize((s, s), Image.LANCZOS), (x, y))
    if not game:
        flag.alpha_composite(layer)
        return flag
    frame, mask, gloss = flag_parts(game, size)
    if not full:
        flag.alpha_composite(layer)
    flag.putalpha(mask)
    flat = flag.copy()
    flag.alpha_composite(frame)
    if full:
        flat.alpha_composite(layer)
        flag.alpha_composite(layer)
        covered = gloss.copy()
        covered.putalpha(ImageChops.multiply(gloss.getchannel("A"), layer.getchannel("A")))
        flag.alpha_composite(covered)
    flag.info["flat"] = flat  # without the frame's shading, for the eyedropper
    return flag


def flag_image(game, name, fit, size, bg=PREVIEW_BG, full=False, cut=True):
    """An upload as the game draws it on the hexagonal flag, at size x size."""
    emblem = emblem_art(Image.open(source(name)).convert("RGBA"), size, fit, full, cut, game) if name else None
    return compose_flag(game, Image.new("RGB", (8, 8), tuple(bg)), emblem, size, full)


def swatch_image(rgb, size):
    im = Image.new("RGBA", (size, size), tuple(rgb) + (255,))
    ImageDraw.Draw(im).rectangle([0, 0, size - 1, size - 1], outline=(255, 255, 255, 90), width=max(1, size // 26))
    return im


def map_image(name, fit, white_map, size, full=False, cut=True, game=None):
    """The emblem as the galaxy map draws it: at 65% opacity over dark space."""
    canvas = Image.new("RGBA", (size, size), MAP_SPACE + (255,))
    if name:
        emblem = map_emblem(emblem_art(Image.open(source(name)).convert("RGBA"), size, fit, full, cut, game), white_map)
        emblem.putalpha(emblem.getchannel("A").point(lambda a: a * 65 // 100))
        canvas.alpha_composite(emblem)
    return canvas


CSS = string.Template("""
.sw-window {
  background-color: #151d1a;
  border-style: solid; border-width: 0;
  border-image-source: url("$panel"); border-image-slice: 80; border-image-width: 80px; border-image-repeat: stretch;
}
.sw-window *:focus { outline-style: none; }
.sw-window label { color: #ffffff; font-family: "URW Gothic", "Century Gothic", sans-serif; font-weight: 600; font-size: 13px; }
.sw-window label.sw-title { font-family: "Malgun Gothic", sans-serif; font-weight: normal; font-size: 22px; }
.sw-window label.sw-name { font-family: "Malgun Gothic", sans-serif; font-weight: normal; font-size: 17px; }
.sw-header {
  background-image: url("$hex"), url("$line");
  background-repeat: no-repeat; background-position: -10px -8px, 6px 30px;
  padding: 6px 4px 22px 16px;
}
/* GTK leaves the middle of a border-image empty, so fill it with the texture's centre colour. */
.sw-dark {
  border-style: solid; border-width: 8px;
  border-image-source: url("$dark"); border-image-slice: 8; border-image-width: 8px; border-image-repeat: stretch;
  background-color: $dark_fill; background-clip: padding-box;
}
.sw-window button.sw-btn {
  background-image: none; box-shadow: none; text-shadow: none; border-radius: 0;
  border-style: solid; border-width: 10px 24px;
  border-image-source: url("$btn"); border-image-slice: 10 24; border-image-width: 10px 24px; border-image-repeat: stretch;
  background-color: $btn_fill; background-clip: padding-box;
  min-height: 14px; min-width: 142px; padding: 0;
}
.sw-window button.sw-btn:hover { border-image-source: url("$btn_hover"); background-color: $btn_hover_fill; }
.sw-window button.sw-btn:active { border-image-source: url("$btn_down"); background-color: $btn_down_fill; }
.sw-window button.sw-btn:disabled { border-image-source: url("$btn_off"); background-color: $btn_off_fill; }
.sw-window button.sw-btn:disabled label { color: #6f7a76; }
.sw-window button.sw-close {
  background-color: transparent; background-image: url("$close"); background-size: 100% 100%;
  border: none; box-shadow: none; min-width: 30px; min-height: 30px; padding: 0;
}
.sw-window button.sw-close:hover { background-image: url("$close_hover"); }
.sw-window button.sw-close:active { background-image: url("$close_down"); }
.sw-window radiobutton radio, .sw-window checkbutton check {
  -gtk-icon-source: url("$check"); -gtk-icon-shadow: none;
  background: none; border: none; box-shadow: none; min-width: 22px; min-height: 22px;
}
.sw-window radiobutton:hover radio, .sw-window checkbutton:hover check { -gtk-icon-source: url("$check_hover"); }
.sw-window radiobutton radio:checked, .sw-window checkbutton check:checked { -gtk-icon-source: url("$check_on"); }
.sw-window radiobutton:disabled label, .sw-window checkbutton:disabled label { color: #6f7a76; }
.sw-window .sw-map { border: 1px solid rgba(31, 224, 202, 0.35); }
.sw-window button.sw-tab { min-width: 92px; }
.sw-window button.sw-tab:checked { border-image-source: url("$btn_hover"); background-color: $btn_hover_fill; }
.sw-window button.sw-tab:not(:checked) label { color: #c6c6c6; }
.sw-window entry {
  background-color: rgba(0, 0, 0, 0.35); background-image: none; color: #ffffff; caret-color: #1fe0ca;
  border: 1px solid rgba(31, 224, 202, 0.35); border-radius: 0; box-shadow: none; padding: 4px 8px;
  font-family: "URW Gothic", "Century Gothic", sans-serif; font-weight: 600; font-size: 13px;
}
.sw-window entry:focus { border-color: rgba(31, 224, 202, 0.9); }
.sw-window entry:disabled { color: #6f7a76; }
.sw-window entry selection { background-color: rgba(31, 224, 202, 0.4); }

.sw-window scrolledwindow, .sw-window viewport, .sw-window flowbox { background: transparent; border: none; }
.sw-window flowboxchild { padding: 4px; border-radius: 0; background: none; }
.sw-window flowboxchild:hover { background-image: url("$hover"); background-size: 100% 100%; }
.sw-window flowboxchild:selected { background-color: transparent; background-image: url("$selected"); background-size: 100% 100%; }
.sw-window scrollbar, .sw-window scrollbar trough { background: transparent; border: none; }
.sw-window scrollbar slider { background-color: rgba(31, 224, 202, 0.45); border: none; border-radius: 0; min-width: 4px; }
""")


def build_theme(game):
    """Cut the game's own UI textures into PNGs and return CSS that uses them."""
    ui = game / "gfx" / "interface"
    CACHE.mkdir(parents=True, exist_ok=True)
    values = {}

    def save(name, im):
        path = CACHE / f"{name}.png"
        im.save(path)
        values[name] = path.as_uri()
        r, g, b, a = im.getpixel((im.width // 2, im.height // 2))
        values[f"{name}_fill"] = f"rgba({r}, {g}, {b}, {a / 255:.3f})"

    def frames(rel, names):
        im = open_image(ui / rel).convert("RGBA")
        w = im.width // len(names)
        for i, name in enumerate(names):
            save(name, im.crop((i * w, 0, (i + 1) * w, im.height)))

    save("panel", open_image(ui / "tiles/subwindow_tile_plain_solid.dds").convert("RGBA"))
    save("dark", open_image(ui / "tiles/dark_area_cut_8.dds").convert("RGBA"))
    save("hex", open_image(ui / "planetview/planet_view_hex_bg.dds").convert("RGBA"))
    save("line", open_image(ui / "planetview/line.dds").convert("RGBA"))
    frames("buttons/button_268_animated.dds", ["btn", "btn_hover", "btn_down"])
    frames("buttons/checkbox_20_20_01.dds", ["check", "check_on", "check_hover"])
    frames("buttons/close_button.dds", ["close", "close_hover", "close_down"])
    normal = Image.open(CACHE / "btn.png")
    save("btn_off", Image.merge("RGBA", (*ImageOps.grayscale(normal).convert("RGB").split(), normal.getchannel("A"))))
    highlight = open_image(ui / "flags/flag_highlight.dds").convert("RGBA")
    save("selected", highlight)
    save("hover", Image.merge("RGBA", (*Image.new("RGB", highlight.size, (31, 224, 202)).split(), highlight.getchannel("A"))))

    # Make the game's header font available to this process only (Windows already has it).
    if sys.platform.startswith("linux"):
        try:
            fontconfig = ctypes.CDLL("libfontconfig.so.1")
            fontconfig.FcConfigAppFontAddFile.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
            fontconfig.FcConfigAppFontAddFile(None, str(game / "gfx/fonts/malgun.ttf").encode())
        except OSError:
            pass
    return CSS.substitute(values)


def build_window():
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("GdkPixbuf", "2.0")
    gi.require_version("Pango", "1.0")
    from gi.repository import Gdk, GdkPixbuf, GLib, Gtk
    import cairo

    game = find_game()
    setup_error = None
    try:
        ensure_mod(game)
        install_desktop_entry()
    except Exception as e:
        setup_error = e
    themed = False
    if game:
        try:
            provider = Gtk.CssProvider()
            provider.load_from_data(build_theme(game).encode())
            Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider,
                                                     Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            themed = True
        except Exception as e:
            print(f"Couldn't load the Stellaris look, using the plain one: {e}", file=sys.stderr)

    win = Gtk.Window(title="Stellaris Flag Uploader", decorated=not themed, resizable=False)
    win.set_position(Gtk.WindowPosition.CENTER)
    win.get_style_context().add_class("sw-window")
    win.connect("destroy", Gtk.main_quit)
    scale = win.get_scale_factor()

    def show_image(widget, im):
        """Show a PIL image rendered at `scale` x its logical size, crisp on HiDPI."""
        widget.flat_image = im.info.get("flat")  # flag previews carry an unshaded copy for the eyedropper
        im = im.convert("RGBA")
        pb = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(im.tobytes()), GdkPixbuf.Colorspace.RGB,
                                             True, 8, im.width, im.height, im.width * 4)
        widget.set_from_surface(Gdk.cairo_surface_create_from_pixbuf(pb, scale, None))

    def styled(widget, *classes):
        for cls in classes:
            widget.get_style_context().add_class(cls)
        return widget

    root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=14, margin_top=8)
    win.add(root)

    # Header: hex pattern, title, divider line and a close button; drag it to move the window.
    header_events = Gtk.EventBox()
    header_events.connect("button-press-event", lambda _, e: win.begin_move_drag(
        e.button, int(e.x_root), int(e.y_root), e.time) if e.button == 1 else None)
    header = styled(Gtk.Box(spacing=8), "sw-header")
    header.pack_start(styled(Gtk.Label(label="Custom Flags", xalign=0), "sw-title"), True, True, 0)
    close = styled(Gtk.Button(relief=Gtk.ReliefStyle.NONE, valign=Gtk.Align.START), "sw-close")
    close.connect("clicked", lambda _: win.destroy())
    close.set_no_show_all(not themed)
    header.pack_end(close, False, False, 0)
    header_events.add(header)
    root.pack_start(header_events, False, False, 0)

    # Two pages, Emblems and Colours, switched by tab buttons under the header.
    tabs = Gtk.Box(spacing=8)
    root.pack_start(tabs, False, False, 0)
    stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
    root.pack_start(stack, True, True, 0)
    emblems_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    stack.add_named(emblems_page, "emblems")

    body = Gtk.Box(spacing=20)
    emblems_page.pack_start(body, True, True, 0)

    # Left: the emblem grid, like the one in the game's flag editor.
    grid_panel = styled(Gtk.Box(), "sw-dark")
    scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
    scroller.set_size_request(5 * (THUMB_SIZE + 8) + 16, 330)
    grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.SINGLE, max_children_per_line=5, min_children_per_line=5,
                       homogeneous=True, valign=Gtk.Align.START, activate_on_single_click=True)
    scroller.add(grid)
    grid_panel.pack_start(scroller, True, True, 0)
    body.pack_start(grid_panel, False, False, 0)

    # Right: the selected emblem on the in-game flag shape.
    side = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    body.pack_start(side, False, False, 0)
    preview = Gtk.Image()
    side.pack_start(preview, False, False, 0)
    name_label = styled(Gtk.Label(), "sw-name")
    side.pack_start(name_label, False, False, 0)
    fill_btn = Gtk.RadioButton.new_with_label(None, "Fill")
    fit_btn = Gtk.RadioButton.new_with_label_from_widget(fill_btn, "Fit")
    fill_btn.set_tooltip_text("Crop the image to fill the square")
    fit_btn.set_tooltip_text("Show the whole image; the gaps show your flag background")
    modes = Gtk.Box(spacing=24, halign=Gtk.Align.CENTER)
    modes.pack_start(fill_btn, False, False, 0)
    modes.pack_start(fit_btn, False, False, 0)
    side.pack_start(modes, False, False, 0)
    map_preview = styled(Gtk.Image(tooltip_text="How it looks on the galaxy map"), "sw-map")
    white_btn = Gtk.CheckButton(label="White on galaxy map")
    white_btn.set_tooltip_text("Like the game's own emblems, which show as white over space. "
                               "Turn off to show the picture in colour.")
    map_row = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER)
    map_row.pack_start(map_preview, False, False, 0)
    map_row.pack_start(white_btn, False, False, 0)
    side.pack_start(map_row, False, False, 0)
    full_btn = Gtk.CheckButton(label="Fill the whole flag",
                               tooltip_text="Cover the whole hexagon instead of the emblem square in its middle")
    spill_btn = Gtk.CheckButton(label="Spill over the border",
                                tooltip_text="Draw anything past the flag's edge over its border instead of cutting it off at the hexagon")
    canvas_row = Gtk.Box(spacing=16, halign=Gtk.Align.CENTER)
    canvas_row.pack_start(full_btn, False, False, 0)
    canvas_row.pack_start(spill_btn, False, False, 0)
    side.pack_start(canvas_row, False, False, 0)
    status = Gtk.Label(wrap=True, max_width_chars=32, justify=Gtk.Justification.CENTER, use_markup=True)
    side.pack_start(status, False, False, 0)

    footer = Gtk.Box(spacing=12)
    remove_btn = styled(Gtk.Button(label="Remove"), "sw-btn")
    upload_btn = styled(Gtk.Button(label="Upload Images"), "sw-btn")
    footer.pack_start(remove_btn, False, False, 0)
    footer.pack_end(upload_btn, False, False, 0)
    emblems_page.pack_start(footer, False, False, 0)

    names = []
    updating = False

    def selected():
        children = grid.get_selected_children()
        return names[children[0].get_index()] if children else None

    def rebuild(select=None, changed=False):
        nonlocal names
        manifest = load_manifest()
        names = sorted(manifest)
        for child in grid.get_children():
            grid.remove(child)
        for name in names:
            thumb = Gtk.Image(tooltip_text=name)
            show_image(thumb, square(Image.open(source(name)).convert("RGBA"), THUMB_SIZE * scale, manifest[name]["fit"]))
            grid.add(thumb)
        grid.show_all()
        if names:
            grid.select_child(grid.get_child_at_index(names.index(select) if select in names else 0))
        show(changed)

    def show(changed=False):
        nonlocal updating
        name = selected()
        info = settings(load_manifest()[name] if name else {})
        shown_game = game if themed else None
        show_image(preview, flag_image(shown_game, name, info["fit"], PREVIEW_SIZE * scale, full=info["full"], cut=info["cut"]))
        show_image(map_preview, map_image(name, info["fit"], info["white_map"], MAP_PREVIEW_SIZE * scale,
                                          info["full"], info["cut"], game))
        name_label.set_text(name or "No images yet")
        updating = True
        (fit_btn if info["fit"] else fill_btn).set_active(True)
        white_btn.set_active(info["white_map"])
        full_btn.set_active(info["full"])
        spill_btn.set_active(not info["cut"])
        updating = False
        for widget in (remove_btn, fill_btn, fit_btn, white_btn, full_btn):
            widget.set_sensitive(bool(name))
        spill_btn.set_sensitive(bool(name) and info["full"])
        if name and info["full"]:
            text = (f"<span foreground='{GREY}'>In Stellaris pick it under </span>"
                    f"<span foreground='{YELLOW}'>Custom</span><span foreground='{GREY}'>. It covers the whole flag"
                    + (", cut off by the hexagon.</span>" if info["cut"] else " and spills over its border.</span>"))
        elif name:
            text = (f"<span foreground='{GREY}'>In Stellaris pick it under </span>"
                    f"<span foreground='{YELLOW}'>Custom</span><span foreground='{GREY}'>. "
                    "The grey edge is where your flag background shows.</span>")
        else:
            text = f"<span foreground='{GREY}'>Click </span><span foreground='{YELLOW}'>Upload Images</span>" \
                   f"<span foreground='{GREY}'> to add some.</span>"
        if changed and game_running():
            text += f"\n\n<span foreground='{RED}'>Stellaris is running. Restart it to see the change.</span>"
        status.set_markup(text)

    def on_upload(_):
        dialog = Gtk.FileChooserDialog(title="Choose images", parent=win, action=Gtk.FileChooserAction.OPEN)
        dialog.add_buttons("_Cancel", Gtk.ResponseType.CANCEL, "_Open", Gtk.ResponseType.OK)
        dialog.set_select_multiple(True)
        images = Gtk.FileFilter()
        images.set_name("Images")
        for ext in EXTS:
            images.add_pattern(f"*.{ext}")
            images.add_pattern(f"*.{ext.upper()}")
        dialog.add_filter(images)
        pictures = Path.home() / "Pictures"
        if pictures.is_dir():
            dialog.set_current_folder(str(pictures))
        paths = dialog.get_filenames() if dialog.run() == Gtk.ResponseType.OK else []
        dialog.destroy()
        last, errors = None, []
        for path in paths:
            try:
                last = upload(path)
            except Exception as e:
                errors.append(f"{Path(path).name}: {e}")
        if paths:
            rebuild(select=last, changed=last is not None)
        if errors:
            status.set_markup(f"<span foreground='{RED}'>Couldn't read:\n"
                              + GLib.markup_escape_text("\n".join(errors)) + "</span>")

    def confirm_remove(name, what):
        dialog = Gtk.Dialog(transient_for=win, modal=True, decorated=not themed)
        styled(dialog, "sw-window")
        box = dialog.get_content_area()
        box.set_spacing(10)
        box.set_border_width(18)
        box.pack_start(styled(Gtk.Label(label=f"Remove {name}?", xalign=0), "sw-name"), False, False, 0)
        box.pack_start(Gtk.Label(label=f"Any empire using this {what} will lose it.", xalign=0), False, False, 0)
        styled(dialog.add_button("Cancel", Gtk.ResponseType.CANCEL), "sw-btn")
        styled(dialog.add_button("Remove", Gtk.ResponseType.OK), "sw-btn")
        dialog.get_action_area().set_border_width(12)
        dialog.show_all()
        ok = dialog.run() == Gtk.ResponseType.OK
        dialog.destroy()
        return ok

    def on_remove(_):
        name = selected()
        if confirm_remove(name, "emblem"):
            remove(name)
            rebuild(changed=True)

    def on_setting(**read):
        def handler(_):
            name = selected()
            if name and not updating:
                try:
                    update(name, **{key: get() for key, get in read.items()})
                except Exception as e:
                    status.set_markup(f"<span foreground='{RED}'>{GLib.markup_escape_text(str(e))}</span>")
                    return
                rebuild(select=name, changed=True)
        return handler

    grid.connect("selected-children-changed", lambda *_: show())
    upload_btn.connect("clicked", on_upload)
    remove_btn.connect("clicked", on_remove)
    fit_btn.connect("toggled", on_setting(fit=fit_btn.get_active))
    white_btn.connect("toggled", on_setting(white_map=white_btn.get_active))
    full_btn.connect("toggled", on_setting(full=full_btn.get_active))
    spill_btn.connect("toggled", on_setting(cut=lambda: not spill_btn.get_active()))
    rebuild()
    if setup_error:
        status.set_markup(f"<span foreground='{RED}'>Couldn't set up the mod: {GLib.markup_escape_text(str(setup_error))}</span>")
    elif not game:
        status.set_markup(f"<span foreground='{RED}'>Stellaris wasn't found. Install it with Steam, then reopen this.</span>")

    # Colours page: your own exact colours, added to the game's colour pickers.
    colours_page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    stack.add_named(colours_page, "colours")
    cbody = Gtk.Box(spacing=20)
    colours_page.pack_start(cbody, True, True, 0)

    swatch_panel = styled(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6), "sw-dark")
    swatch_scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
    swatch_scroller.set_size_request(5 * (THUMB_SIZE + 8) + 16, 300)
    swatches = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.SINGLE, max_children_per_line=5, min_children_per_line=5,
                           homogeneous=True, valign=Gtk.Align.START, activate_on_single_click=True)
    swatch_scroller.add(swatches)
    swatch_panel.pack_start(swatch_scroller, True, True, 0)
    count_label = Gtk.Label(xalign=0, use_markup=True)
    swatch_panel.pack_start(count_label, False, False, 0)
    cbody.pack_start(swatch_panel, False, False, 0)

    cside = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    cbody.pack_start(cside, False, False, 0)
    colour_preview = Gtk.Image(tooltip_text="Your colour as a solid flag background")
    cside.pack_start(colour_preview, False, False, 0)
    name_entry = Gtk.Entry(placeholder_text="Colour name", max_length=40)
    cside.pack_start(name_entry, False, False, 0)

    # Classic picker: saturation left to right and brightness top to bottom in the
    # square, hue along the bar under it.
    sv_area = Gtk.DrawingArea(tooltip_text="Saturation and brightness")
    sv_area.set_size_request(PICKER_WIDTH, PICKER_HEIGHT)  # includes the ring margin
    hue_area = Gtk.DrawingArea(tooltip_text="Hue")
    hue_area.set_size_request(PICKER_WIDTH, HUE_BAR_HEIGHT)
    for area in (sv_area, hue_area):
        area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON1_MOTION_MASK)
        cside.pack_start(area, False, False, 0)
    code_row = Gtk.Box(spacing=10)
    code_entry = Gtk.Entry(max_length=24, width_chars=11, tooltip_text="Type a hex code (#FF8800) or rgb(255, 136, 0)")
    rgb_label = Gtk.Label(xalign=0, use_markup=True)
    colour_dropper = dropper_button(styled)
    code_row.pack_start(code_entry, False, False, 0)
    code_row.pack_start(colour_dropper, False, False, 0)
    code_row.pack_start(rgb_label, False, False, 0)
    cside.pack_start(code_row, False, False, 0)
    colour_status = Gtk.Label(wrap=True, max_width_chars=34, justify=Gtk.Justification.CENTER, use_markup=True)
    cside.pack_start(colour_status, False, False, 0)

    cfooter = Gtk.Box(spacing=12)
    remove_colour_btn = styled(Gtk.Button(label="Remove"), "sw-btn")
    new_colour_btn = styled(Gtk.Button(label="New Colour"), "sw-btn")
    cfooter.pack_start(remove_colour_btn, False, False, 0)
    cfooter.pack_end(new_colour_btn, False, False, 0)
    colours_page.pack_start(cfooter, False, False, 0)

    colours = []
    colour_busy = False
    pending_save = None
    hsv = [170.0, 80.0, 75.0]  # picker position, kept apart from the rgb so greys keep their hue

    def outline(cr, x, y, w, h):
        cr.set_source_rgba(31 / 255, 224 / 255, 202 / 255, 0.45)
        cr.set_line_width(1)
        cr.rectangle(x + 0.5, y + 0.5, w - 1, h - 1)
        cr.stroke()

    RING = 8  # the square is inset by this much so the ring isn't cut off at its edges

    def sv_box(area):
        return RING, RING, area.get_allocated_width() - 2 * RING, area.get_allocated_height() - 2 * RING

    def draw_sv(area, cr):
        x0, y0, w, h = sv_box(area)
        cr.translate(x0, y0)
        cr.rectangle(0, 0, w, h)
        cr.clip()
        cr.set_source_rgb(*(c / 255 for c in hsv_to_rgb(hsv[0], 100, 100)))
        cr.paint()
        for x1, y1, start, end in ((w, 0, (1, 1, 1, 1), (1, 1, 1, 0)), (0, h, (0, 0, 0, 0), (0, 0, 0, 1))):
            gradient = cairo.LinearGradient(0, 0, x1, y1)
            gradient.add_color_stop_rgba(0, *start)
            gradient.add_color_stop_rgba(1, *end)
            cr.set_source(gradient)
            cr.paint()
        outline(cr, 0, 0, w, h)
        cr.reset_clip()
        if current_colour():
            x, y = hsv[1] / 100 * w, (1 - hsv[2] / 100) * h
            cr.set_line_width(1.5)
            cr.set_source_rgb(0, 0, 0)
            cr.arc(x, y, 7, 0, 2 * math.pi)
            cr.stroke()
            cr.set_source_rgb(1, 1, 1)
            cr.arc(x, y, 5.5, 0, 2 * math.pi)
            cr.stroke()
        return False

    def draw_hue(area, cr):
        h = area.get_allocated_height()
        w = area.get_allocated_width() - 2 * RING  # lined up with the square above
        inset = 4  # the handle sticks out above and below the bar
        cr.translate(RING, 0)
        gradient = cairo.LinearGradient(0, 0, w, 0)
        for i in range(7):
            gradient.add_color_stop_rgb(i / 6, *(c / 255 for c in hsv_to_rgb(i * 60, 100, 100)))
        cr.rectangle(0, inset, w, h - 2 * inset)
        cr.set_source(gradient)
        cr.fill()
        outline(cr, 0, inset, w, h - 2 * inset)
        if current_colour():
            x = hsv[0] / 360 * w
            cr.rectangle(x - 3.5, 0.5, 7, h - 1)
            cr.set_source_rgb(233 / 255, 1, 251 / 255)
            cr.fill_preserve()
            cr.set_source_rgb(31 / 255, 224 / 255, 202 / 255)
            cr.set_line_width(1)
            cr.stroke()
        return False

    def current_colour():
        children = swatches.get_selected_children()
        return colours[children[0].get_index()] if children else None

    def rebuild_colours(select=None, changed=False, error=None):
        nonlocal colours
        colours = load_colours()
        for child in swatches.get_children():
            swatches.remove(child)
        for c in colours:
            tile = Gtk.Image(tooltip_text=c["name"])
            show_image(tile, swatch_image(c["rgb"], THUMB_SIZE * scale))
            swatches.add(tile)
        swatches.show_all()
        keys = [c["key"] for c in colours]
        if colours:
            swatches.select_child(swatches.get_child_at_index(keys.index(select) if select in keys else 0))
        show_colour(changed, error)

    def show_rgb(rgb, update_code=True):
        nonlocal colour_busy
        show_image(colour_preview, flag_image(game if themed else None, None, False, COLOUR_PREVIEW_SIZE * scale, bg=rgb))
        if update_code:
            colour_busy = True
            code_entry.set_text(hex_code(rgb))
            colour_busy = False
        rgb_label.set_markup(f"<span foreground='{GREY}'>rgb({rgb[0]}, {rgb[1]}, {rgb[2]})</span>")
        sv_area.queue_draw()
        hue_area.queue_draw()

    def show_colour(changed=False, error=None):
        nonlocal colour_busy
        c = current_colour()
        rgb = tuple(c["rgb"]) if c else PREVIEW_BG
        if c:
            hsv[:] = rgb_to_hsv(rgb)
        show_rgb(rgb)
        colour_busy = True
        name_entry.set_text(c["name"] if c else "")
        if not c:
            code_entry.set_text("")
            rgb_label.set_text("")
        colour_busy = False
        for widget in (name_entry, code_entry, remove_colour_btn, sv_area, hue_area, colour_dropper):
            widget.set_sensitive(bool(c))
        new_colour_btn.set_sensitive(len(colours) < MAX_COLOURS and game is not None)
        count_label.set_markup(f"<span foreground='{GREY}'>{len(colours)} / {MAX_COLOURS} colours</span>")
        if error:
            text = f"<span foreground='{RED}'>Couldn't update the game files: {GLib.markup_escape_text(str(error))}</span>"
        elif c:
            text = (f"<span foreground='{GREY}'>Pick it in the flag editor for flag or map colours, "
                    "or on the ship screen for ship colour.</span>")
        else:
            text = (f"<span foreground='{GREY}'>Click </span><span foreground='{YELLOW}'>New Colour</span>"
                    f"<span foreground='{GREY}'> to make one.</span>")
        if len(colours) >= MAX_COLOURS:
            text += f"\n<span foreground='{YELLOW}'>That's as many as the game's colour grids can fit.</span>"
        if changed and game_running():
            text += f"\n\n<span foreground='{RED}'>Stellaris is running. Restart it to see the change.</span>"
        colour_status.set_markup(text)

    def save_and_write():
        """Save colours and update the game files; returns the error, if any."""
        save_colours(colours)
        try:
            write_colour_files(game)
        except Exception as e:
            return e

    def on_save_timeout():
        nonlocal pending_save
        pending_save = None
        show_colour(changed=True, error=save_and_write())
        return False

    def schedule_commit():
        """Edits are saved shortly after the last change, not on every drag step."""
        nonlocal pending_save
        if pending_save:
            GLib.source_remove(pending_save)
        pending_save = GLib.timeout_add(400, on_save_timeout)

    def flush():
        nonlocal pending_save
        if pending_save:
            GLib.source_remove(pending_save)
            pending_save = None
            save_and_write()

    def set_colour(rgb, source):
        """Apply a new rgb from the picker ("picker") or the code box ("code")."""
        c = current_colour()
        c["rgb"] = list(rgb)
        show_image(swatches.get_child_at_index(colours.index(c)).get_child(), swatch_image(rgb, THUMB_SIZE * scale))
        if source != "picker":
            hsv[:] = rgb_to_hsv(rgb)
        show_rgb(rgb, update_code=source != "code")
        schedule_commit()

    def clamp(value):
        return min(max(value, 0.0), 1.0)

    def picking(event):
        """A left click, or moving with the left button held. Plain hovering doesn't count
        (the tooltips make GTK report every mouse movement)."""
        if event.type == Gdk.EventType.BUTTON_PRESS:
            return event.button == 1
        return bool(event.state & Gdk.ModifierType.BUTTON1_MASK)

    def on_pick_sv(area, event):
        if picking(event) and current_colour():
            x0, y0, w, h = sv_box(area)
            hsv[1] = clamp((event.x - x0) / w) * 100
            hsv[2] = (1 - clamp((event.y - y0) / h)) * 100
            set_colour(hsv_to_rgb(*hsv), "picker")
        return True

    def on_pick_hue(area, event):
        if picking(event) and current_colour():
            hsv[0] = clamp((event.x - RING) / (area.get_allocated_width() - 2 * RING)) * 360
            set_colour(hsv_to_rgb(*hsv), "picker")
        return True

    def on_dropper(_):
        def hover(rgb):  # preview without saving
            show_rgb(rgb)
            colour_status.set_markup(f"<span foreground='{GREY}'>Click to take </span><span foreground='{YELLOW}'>"
                                     f"{hex_code(rgb)}</span><span foreground='{GREY}'>. Esc cancels.</span>")

        def done(picked):
            if not picked:
                show_colour()
        if current_colour() and pick_screen_colour(win, lambda rgb: set_colour(rgb, "dropper"), hover, done):
            colour_status.set_markup(f"<span foreground='{GREY}'>Click anywhere on screen to take its colour. Esc cancels.</span>")

    def on_code(_):
        rgb = parse_colour_code(code_entry.get_text())
        if rgb and not colour_busy and current_colour():
            set_colour(rgb, "code")

    def on_name(_):
        c = current_colour()
        if not colour_busy and c:
            c["name"] = name_entry.get_text().strip() or "Custom Colour"
            swatches.get_child_at_index(colours.index(c)).get_child().set_tooltip_text(c["name"])
            schedule_commit()

    def on_new_colour(_):
        flush()
        key = new_colour_key(colours)
        colours.append({"key": key, "name": f"Custom Colour {key.rsplit('_', 1)[1]}", "rgb": list(hsv_to_rgb(170, 80, 75))})
        rebuild_colours(select=key, changed=True, error=save_and_write())

    def on_remove_colour(_):
        flush()
        c = current_colour()
        if confirm_remove(c["name"], "colour"):
            colours.remove(c)
            rebuild_colours(changed=True, error=save_and_write())

    swatches.connect("selected-children-changed", lambda *_: None if colour_busy else show_colour())
    sv_area.connect("draw", draw_sv)
    hue_area.connect("draw", draw_hue)
    for area, handler in ((sv_area, on_pick_sv), (hue_area, on_pick_hue)):
        area.connect("button-press-event", handler)
        area.connect("motion-notify-event", handler)
    code_entry.connect("changed", on_code)
    colour_dropper.connect("clicked", on_dropper)
    name_entry.connect("changed", on_name)
    new_colour_btn.connect("clicked", on_new_colour)
    remove_colour_btn.connect("clicked", on_remove_colour)
    win.connect("destroy", lambda _: flush())

    # Keep the game files in step with the installed game (it may have been updated).
    startup_error = None
    if load_colours():
        try:
            write_colour_files(game)
        except Exception as e:
            startup_error = e
    rebuild_colours()
    if startup_error:
        show_colour(error=startup_error)
    try:
        write_flag_shader(game)  # only present while an emblem fills the whole flag
    except Exception as e:
        status.set_markup(f"<span foreground='{RED}'>{GLib.markup_escape_text(str(e))}</span>")

    # Designer and Maker tabs live in flag_studio.py, next to this file.
    if str(APP) not in sys.path:
        sys.path.insert(0, str(APP))
    import flag_studio
    studio_css = Gtk.CssProvider()
    studio_css.load_from_data(flag_studio.CSS.encode())
    Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), studio_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    ui = types.SimpleNamespace(win=win, game=game, themed=themed, scale=scale, show_image=show_image, styled=styled)
    studio_data = flag_studio.GameData(types.SimpleNamespace(**globals()), game)
    designer = flag_studio.DesignerPage(ui, studio_data)
    maker = flag_studio.MakerPage(ui, studio_data)
    stack.add_named(maker.widget, "maker")
    stack.add_named(designer.widget, "designer")
    win.studio = types.SimpleNamespace(designer=designer, maker=maker)  # handle for scripted tests
    # Tabs pick up what the others changed (uploads, colours, saved designs) when shown.
    refreshers = {"emblems": lambda: rebuild(select=selected()), "maker": maker.refresh, "designer": designer.refresh}
    stack.connect("notify::visible-child-name", lambda s, _: refreshers.get(s.get_visible_child_name(), lambda: None)())

    tab_buttons = {}
    switching = False

    def switch(page):
        nonlocal switching
        if switching:
            return
        switching = True
        for name, button in tab_buttons.items():
            button.set_active(name == page)
        switching = False
        stack.set_visible_child_name(page)

    for page, label in (("emblems", "Emblems"), ("colours", "Colours"), ("maker", "Maker"), ("designer", "Designer")):
        tab = styled(Gtk.ToggleButton(label=label), "sw-btn", "sw-tab")
        tab.connect("clicked", lambda _, p=page: switch(p))
        tabs.pack_start(tab, False, False, 0)
        tab_buttons[page] = tab
    switch("emblems")
    return win


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    action = ap.add_mutually_exclusive_group()
    action.add_argument("--upload", type=Path, nargs="+", metavar="IMAGE", help="add images as new emblems")
    action.add_argument("--remove", metavar="NAME", help="remove an uploaded emblem")
    action.add_argument("--list", action="store_true", help="list uploaded emblems")
    action.add_argument("--check", action="store_true", help="show where the game and the mod are")
    action.add_argument("--self-test", action="store_true", help="open every tab and close again (checks a build starts)")
    ap.add_argument("--fit", action="store_true", help="with --upload: pad to a square instead of cropping")
    ap.add_argument("--colour-map", action="store_true", help="with --upload: keep colours on the galaxy map")
    args = ap.parse_args()
    game = find_game()
    if args.check:
        import PIL
        print(f"Python {sys.version.split()[0]} at {sys.executable}\nPillow {PIL.__version__} from {Path(PIL.__file__).parent}")
        try:
            import gi
            gi.require_version("Gtk", "3.0")
            from gi.repository import Gtk
            import cairo
            print(f"GTK {Gtk.get_major_version()}.{Gtk.get_minor_version()}.{Gtk.get_micro_version()} via {Path(gi.__file__).parent}"
                  f"\npycairo {cairo.version} from {Path(cairo.__file__).parent}")
            if sys.platform.startswith("linux"):  # which GTK library actually got loaded
                maps = Path("/proc/self/maps").read_text()
                print("libgtk-3:", next((l.split()[-1] for l in maps.splitlines() if "libgtk-3.so" in l), "not loaded"))
        except Exception as e:
            print(f"GTK unavailable: {e}")
        print(f"Stellaris: {game or 'not found'}\nMod folder: {MOD}")
        return
    ensure_mod(game)

    if args.upload:
        for path in args.upload:
            print(f"Added {path.name} as {upload(path, args.fit, False if args.colour_map else None)}")
        if game_running():
            print("Restart Stellaris to see the change.")
    elif args.remove:
        if args.remove not in load_manifest():
            sys.exit(f"No uploaded emblem named {args.remove}")
        remove(args.remove)
        print(f"Removed {args.remove}")
    elif args.self_test:
        win = build_window()
        from gi.repository import GLib, Gtk
        win.show_all()
        GLib.timeout_add(1500, Gtk.main_quit)
        Gtk.main()
        print("self-test ok")
    elif args.list:
        for name, info in sorted(load_manifest().items()):
            info = settings(info)
            whole = (", whole flag" + (", cut to hexagon" if info["cut"] else ", past the border")) if info["full"] else ""
            print(f"{name}  ({'fit' if info['fit'] else 'fill'}, {'white' if info['white_map'] else 'colour'} on map{whole})")
    else:
        win = build_window()
        from gi.repository import Gtk
        win.show_all()
        Gtk.main()


if __name__ == "__main__":
    main()
