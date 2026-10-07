#!/usr/bin/python3
"""Builds the Steam Workshop version of the mod: a clean "showcase" mod with the
brighter emblem picker, example emblems and backgrounds, a thumbnail and a
descriptor, in <Stellaris mod folder>/custom_flags_workshop. Upload it from the
Paradox launcher (Mods → Upload mod), with description.bbcode as its description.

It leaves out what the app manages per player: custom colours (the game reads only
one colour list, so a Workshop copy would replace the app's) and the patched flag
shader for full-flag emblems (a frozen copy could break flags after a game update).
Example files use a cfx_ prefix so they never collide with the app's uploads.

  python3 packaging/workshop/build_workshop.py [--out FOLDER]
"""
import argparse
import importlib.util
import math
import os
import random
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NAME = "Custom Flags - Your Own Flags, Emblems & Colours"
TAGS = ["Graphics", "Utilities"]

# Example emblems, as Maker layers (shape, colour, x, y, size, rotation).
EMBLEMS = {
    "cfx_star_compass": [("Burst", (232, 182, 64), .5, .5, .9, 0), ("Circle", (20, 34, 82), .5, .5, .6, 0),
                         ("Star", (250, 248, 236), .5, .51, .48, 0)],
    "cfx_machine_cog": [("Cog", (70, 205, 185), .5, .5, .9, 0), ("Hex ring", (240, 250, 248), .5, .5, .44, 0)],
    "cfx_crescent_star": [("Crescent", (236, 240, 245), .45, .5, .86, -20), ("Star", (238, 190, 70), .66, .4, .3, 0)],
    "cfx_chevron_crest": [("Hexagon", (120, 28, 36), .5, .5, .9, 0), ("Chevron", (236, 190, 80), .5, .48, .62, 0),
                          ("Chevron", (250, 246, 236), .5, .66, .42, 0)],
    "cfx_twin_moons": [("Circle", (236, 240, 245), .36, .5, .42, 0), ("Circle", (150, 190, 236), .66, .5, .42, 0),
                       ("Ring", (236, 240, 245), .5, .5, .94, 0)],
    "cfx_rising_arrow": [("Burst", (226, 96, 50), .5, .5, .9, 0), ("Arrow", (250, 246, 236), .5, .5, .66, -90)],
}
# Example backgrounds: base slot, then (shape, slot, x, y, size, stretch, rotation).
BACKGROUNDS = {
    "cfx_horizon": ("primary", [("Half", "secondary", .5, .62, 1.6, 0, 0), ("Burst", "secondary", .5, .62, .7, 0, 0),
                                ("Bar", "black", .5, .62, .25, 2.1, 0)]),
    "cfx_diagonal_band": ("primary", [("Bar", "secondary", .5, .5, 1.2, 1.5, 45)]),
    "cfx_quarters": ("primary", [("Square", "secondary", .25, .25, .5, 0, 0), ("Square", "secondary", .75, .75, .5, 0, 0)]),
    "cfx_ring": ("primary", [("Ring", "secondary", .5, .5, .74, 0, 0), ("Circle", "secondary", .5, .5, .2, 0, 0)]),
    "cfx_chevron": ("primary", [("Chevron", "secondary", .5, .6, 1.3, 0, 0)]),
}
# Thumbnail flags: (background, primary, secondary, emblem).
THUMB_FLAGS = [("cfx_horizon", (112, 52, 190), (245, 140, 40), "cfx_star_compass"),
               ("cfx_ring", (14, 30, 78), (70, 205, 185), "cfx_machine_cog"),
               ("cfx_diagonal_band", (120, 28, 36), (236, 190, 80), "cfx_crescent_star")]


def load_app(out):
    os.environ["CUSTOM_FLAGS_MOD"] = str(out)  # the app's paths (OUT, SIZES...) now point at the Workshop folder
    sys.path.insert(0, str(ROOT / "app"))
    spec = importlib.util.spec_from_file_location("flag_uploader", ROOT / "app" / "flag_uploader.py")
    core = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(core)
    import gi
    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    gi.require_version("Pango", "1.0")
    import flag_studio
    return core, flag_studio


def layer(studio, shape, x, y, size, rot=0, stretch=0, **colour):
    return dict(studio.new_layer(shape, "background" if "slot" in colour else "emblem", "primary"),
                x=x, y=y, size=size, rot=rot, stretch=stretch, original=False, **colour)


def thumbnail(core, studio, game, emblems, backgrounds):
    from PIL import Image, ImageDraw, ImageFilter, ImageFont
    size, rng = 512, random.Random(3)
    thumb = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(thumb)
    for y in range(size):
        t = y / size
        draw.line([(0, y), (size, y)], fill=(round(10 + 22 * t), round(12 + 10 * t), round(34 + 30 * (1 - t))))
    for _ in range(180):
        x, y, b = rng.randrange(size), rng.randrange(size), rng.randint(120, 255)
        draw.point((x, y), fill=(b, b, min(255, b + 20)))
    glow = Image.new("RGBA", (size, size))
    ImageDraw.Draw(glow).ellipse([60, 120, 452, 420], fill=(31, 224, 202, 40))
    thumb.paste(glow.filter(ImageFilter.GaussianBlur(70)), (0, 0), glow.filter(ImageFilter.GaussianBlur(70)))
    for i, (bg, primary, secondary, emblem) in enumerate(THUMB_FLAGS):
        flag_size = 230 if i == 1 else 180
        background = studio.colourize(backgrounds[bg], primary, secondary)
        flag = core.compose_flag(game, background, emblems[emblem], flag_size)
        x = (size - flag_size) // 2 + (i - 1) * 150
        thumb.paste(flag, (x, 250 - flag_size // 2 + (0 if i == 1 else 18)), flag)
    fonts = game / "gfx" / "fonts"
    title = ImageFont.truetype(str(fonts / "Orbitron-Regular.ttf"), 50)
    small = ImageFont.truetype(str(fonts / "Orbitron-Regular.ttf"), 19)
    w = draw.textlength("CUSTOM FLAGS", font=title)
    draw.text(((size - w) / 2 + 2, 394), "CUSTOM FLAGS", font=title, fill=(0, 0, 0))
    draw.text(((size - w) / 2, 392), "CUSTOM FLAGS", font=title, fill=(240, 252, 250))
    parts, gap = ["Your own images", "any colour", "flag maker"], 26  # Orbitron has no middle dot, so draw them
    x = (size - sum(draw.textlength(p, font=small) for p in parts) - gap * (len(parts) - 1)) / 2
    for i, part in enumerate(parts):
        draw.text((x + 1, 457), part, font=small, fill=(0, 0, 0))
        draw.text((x, 456), part, font=small, fill=(251, 170, 41))
        x += draw.textlength(part, font=small)
        if i < len(parts) - 1:
            draw.ellipse([x + gap / 2 - 3, 466 - 3, x + gap / 2 + 3, 466 + 3], fill=(31, 224, 202))
            x += gap
    return thumb


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=Path, help="folder to build into (default: <Stellaris mod folder>/custom_flags_workshop)")
    args = ap.parse_args()
    sys.path.insert(0, str(ROOT / "app"))
    if args.out:
        out = args.out.resolve()
    else:
        spec = importlib.util.spec_from_file_location("probe", ROOT / "app" / "flag_uploader.py")
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        out = probe.stellaris_user_dir() / "mod" / "custom_flags_workshop"
    if out.exists():  # only ever replace an earlier build of this
        desc = out / "descriptor.mod"
        if not desc.exists() or NAME not in desc.read_text(encoding="utf-8"):
            sys.exit(f"{out} exists and isn't this Workshop build; not touching it")
        shutil.rmtree(out)
    core, studio = load_app(out)
    game = core.find_game()
    if not game:
        sys.exit("Stellaris wasn't found")

    # The mod's own files, as the app makes them (minus uploads/ and the app README).
    core.write_if_changed(core.OUT / "usage.txt", core.USAGE)
    core.write_if_changed(out / "localisation/english/custom_flags_l_english.yml", core.CATEGORY_LOC)
    core.write_if_changed(out / "interface/custom_flags.gfx", core.SLOT_GFX)
    core.write_if_changed(out / "gfx/interface/custom_flags/emblem_slot_bg.dds", core.dds_bytes([core.emblem_slot_bg()]))
    shader, gui = core.slot_files(game)
    core.write_if_changed(out / "gfx/FX/custom_flags_emblem_slot.shader", shader)
    core.write_if_changed(out / "interface/zz_custom_flags.gui", gui)

    emblems = {}
    for name, layers in EMBLEMS.items():
        project = {"kind": "emblem", "layers": [layer(studio, s, x, y, z, r, rgb=list(c)) for s, c, x, y, z, r in layers]}
        art = studio.render_project(None, project, 512, lambda l: tuple(l["rgb"]))
        emblems[name] = art
        for folder, px in core.SIZES.items():
            sq = art.resize((px, px), core.Image.LANCZOS)
            levels = core.mip_chain(core.map_emblem(sq, True)) if folder.name == "map" else [sq]
            core.write_if_changed(folder / f"{name}.dds", core.dds_bytes(levels))
    backgrounds = {}
    for name, (base, layers) in BACKGROUNDS.items():
        project = {"kind": "background", "base": base,
                   "layers": [layer(studio, s, x, y, z, r, stretch=st, slot=slot) for s, slot, x, y, z, st, r in layers]}
        art = studio.render_project(None, project, 400, lambda l: studio.CHANNELS[l["slot"] if l else project["base"]])
        backgrounds[name] = art.convert("RGB")
        core.write_if_changed(out / "flags/backgrounds" / f"{name}.dds", core.dds_bytes([art]))

    thumbnail(core, studio, game, emblems, backgrounds).save(out / "thumbnail.png", optimize=True)
    version = core.descriptor(game).split('supported_version="')[1].split('"')[0]
    descriptor = (f'version="1.0.0"\ntags={{\n' + "".join(f'\t"{t}"\n' for t in TAGS) + "}\n"
                  f'name="{NAME}"\npicture="thumbnail.png"\nsupported_version="{version}"\n')
    core.write_if_changed(out / "descriptor.mod", descriptor)
    core.write_if_changed(out.parent / f"{out.name}.mod", descriptor + f'path="{out.as_posix()}"\n')
    print(f"Built {out}")


if __name__ == "__main__":
    main()
