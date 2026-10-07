#!/usr/bin/python3
"""Makes the Steam Workshop page images (1280x720 cards) in build/workshop-images:
a title card, feature cards from docs/screenshots, what the Workshop mod includes,
and how to get it working. Needs Stellaris installed (fonts, flag frame) and a
Workshop build in the Stellaris mod folder (packaging/workshop/build_workshop.py).

  python3 packaging/workshop/make_cards.py
"""
import importlib.util
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "build" / "workshop-images"
SHOTS = ROOT / "docs" / "screenshots"
W, H = 1280, 720
CYAN, ORANGE, WHITE, GREY = (31, 224, 202), (251, 170, 41), (236, 246, 244), (170, 184, 180)
LINK = "github.com/idiotgamster420/stellaris-custom-flags"

FEATURES = [
    ("emblems", "Use your own images", "Any picture becomes an emblem",
     ["Upload PNG, JPG, WebP and more", "Fill the whole flag, cut to the hexagon",
      "Shows under Custom in the flag editor", "Galaxy map version made for you"]),
    ("colours", "Pick any colour", "Not just the built-in swatches",
     ["Colour picker, hex codes or rgb()", "Eyedropper takes colours from anywhere",
      "For flags, map borders and ships", "Up to 18 colours of your own"]),
    ("maker", "Design flag backgrounds", "Layer editor, Black Ops 2 style",
     ["Stack shapes, game emblems and images", "Move, resize, rotate, stretch, flip",
      "Follows your empire's colours in game", "Up to 32 layers"]),
    ("emblem_maker", "Make your own emblems", "The same editor, in any colours",
     ["Design over the whole flag", "Cut neatly by the hexagon",
      "Or let it spill over the border", "Saved straight into the mod"]),
    ("designer", "Plan your flag", "See it exactly as Stellaris draws it",
     ["Mix backgrounds, colours and emblems", "Uses your own creations too",
      "Tells you what to pick in game"]),
]
STEPS = [
    ("Subscribe and enable", "Add this mod to your playset in the Paradox launcher."),
    ("Get the free app", "Windows installer or Linux AppImage:\n" + LINK),
    ("Open the app once", 'It creates a local mod, also called "Custom Flags".\nRestart the launcher and add it to your playset too.'),
    ("Make your flag", "Upload images, make colours, backgrounds, emblems.\nRestart Stellaris, then look under Custom."),
]


def load():
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


def fonts(game):
    from PIL import ImageFont
    folder = game / "gfx" / "fonts"

    def jura(size, weight="SemiBold"):
        font = ImageFont.truetype(str(folder / "Jura-VariableFont_wght.ttf"), size)
        try:
            font.set_variation_by_name(weight)
        except (OSError, ValueError):
            pass
        return font
    return lambda size: ImageFont.truetype(str(folder / "Orbitron-Regular.ttf"), size), jura


def backdrop(seed):
    from PIL import Image, ImageDraw, ImageFilter
    card = Image.new("RGB", (W, H))
    draw = ImageDraw.Draw(card)
    for y in range(H):
        t = y / H
        draw.line([(0, y), (W, y)], fill=(round(9 + 16 * t), round(12 + 10 * t), round(30 + 26 * (1 - t))))
    rng = random.Random(seed)
    for _ in range(260):
        b = rng.randint(110, 255)
        draw.point((rng.randrange(W), rng.randrange(H)), fill=(b, b, min(255, b + 20)))
    glow = Image.new("RGBA", (W, H))
    ImageDraw.Draw(glow).ellipse([-200, 100, 700, 700], fill=CYAN + (34,))
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    card.paste(glow, (0, 0), glow)
    return card


def text_block(draw, x, y, lines, font, fill, spacing=10):
    for line in lines:
        draw.text((x + 2, y + 2), line, font=font, fill=(0, 0, 0))
        draw.text((x, y), line, font=font, fill=fill)
        y += font.size + spacing
    return y


def wrap(draw, text, font, width):
    lines, line = [], ""
    for word in text.split():
        if line and draw.textlength(f"{line} {word}", font=font) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return lines + [line]


def fitted(draw, text, make_font, size, width):
    """The largest font (from size down) that fits text in width."""
    while size > 18 and draw.textlength(text, font=make_font(size)) > width:
        size -= 2
    return make_font(size)


def framed(shot, height):
    from PIL import Image, ImageDraw, ImageFilter
    shot = shot.convert("RGB").resize((round(shot.width * height / shot.height), height), Image.LANCZOS)
    pad = 24
    out = Image.new("RGBA", (shot.width + pad * 2, shot.height + pad * 2))
    shadow = Image.new("RGBA", out.size)
    ImageDraw.Draw(shadow).rectangle([pad, pad + 6, pad + shot.width, pad + shot.height + 6], fill=(0, 0, 0, 170))
    out.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(10)))
    out.paste(shot, (pad, pad))
    ImageDraw.Draw(out).rectangle([pad - 1, pad - 1, pad + shot.width, pad + shot.height], outline=CYAN + (150,), width=2)
    return out, pad


def footer(draw, jura, text="Free app for Windows & Linux: " + LINK):
    font = jura(20)
    draw.text((W - 40 - draw.textlength(text, font=font), H - 40), text, font=font, fill=GREY)


def example_flags(core, studio, game, workshop):
    """The Workshop mod's own example emblems and backgrounds, drawn as flags."""
    pairs = [((112, 52, 190), (245, 140, 40)), ((14, 30, 78), (70, 205, 185)), ((120, 28, 36), (236, 190, 80)),
             ((30, 90, 60), (230, 230, 220)), ((44, 44, 52), (210, 70, 60))]
    from PIL import Image
    emblems = [core.open_image(p).convert("RGBA") for p in sorted((workshop / "flags/custom_flags").glob("cfx_*.dds"))]
    backgrounds = [core.open_image(p) for p in sorted((workshop / "flags/backgrounds").glob("cfx_*.dds"))]
    plain = Image.new("RGB", (8, 8), (40, 52, 60))
    return ([core.compose_flag(game, plain, e, 170) for e in emblems],
            [core.compose_flag(game, studio.colourize(b, *pairs[i % len(pairs)]), None, 170) for i, b in enumerate(backgrounds)],
            emblems, backgrounds, pairs)


def main():
    from PIL import ImageDraw
    core, studio = load()
    game = core.find_game()
    workshop = core.stellaris_user_dir() / "mod" / "custom_flags_workshop"
    if not game or not workshop.exists():
        sys.exit("Needs Stellaris and a Workshop build (packaging/workshop/build_workshop.py)")
    orbitron, jura = fonts(game)
    OUT.mkdir(parents=True, exist_ok=True)
    emblem_flags, background_flags, emblems, backgrounds, pairs = example_flags(core, studio, game, workshop)
    cards = []

    card = backdrop(1)  # title
    draw = ImageDraw.Draw(card)
    for i, (bg, emblem, size) in enumerate(((0, 0, 260), (3, 1, 330), (1, 2, 260))):
        flag = core.compose_flag(game, studio.colourize(backgrounds[[2, 4, 1, 3, 0][bg]], *pairs[i * 2 % 5]), emblems[[5, 4, 1][emblem]], size)
        card.paste(flag, (W // 2 - size // 2 + (i - 1) * 300, 300 - size // 2 + (0 if i == 1 else 26)), flag)
    title = orbitron(84)
    tw = draw.textlength("CUSTOM FLAGS", font=title)
    text_block(draw, (W - tw) / 2, 500, ["CUSTOM FLAGS"], title, WHITE)
    sub = "Your own images  ·  any colour  ·  flag & emblem maker"
    parts, gap, font = sub.split("  ·  "), 34, jura(32)
    x = (W - sum(draw.textlength(p, font=font) for p in parts) - gap * (len(parts) - 1)) / 2
    for i, part in enumerate(parts):
        text_block(draw, x, 612, [part], font, ORANGE)
        x += draw.textlength(part, font=font)
        if i < len(parts) - 1:
            draw.ellipse([x + gap / 2 - 4, 632 - 4, x + gap / 2 + 4, 632 + 4], fill=CYAN)
            x += gap
    cards.append(("01_title", card))

    for n, (shot, heading, sub, bullets) in enumerate(FEATURES, start=2):
        from PIL import Image
        card = backdrop(n)
        draw = ImageDraw.Draw(card)
        image, pad = framed(Image.open(SHOTS / f"{shot}.png"), 560)
        card.paste(image, (40 - pad + 24, (H - image.height) // 2 - 16), image)
        x = 40 + image.width - pad + 30
        width = W - x - 40
        y = text_block(draw, x, 120, [heading], fitted(draw, heading, orbitron, 40, width), WHITE)
        y = text_block(draw, x, y + 6, wrap(draw, sub, jura(29), width), jura(29), ORANGE) + 26
        for bullet in bullets:
            draw.polygon([(x + 4, y + 13), (x + 13, y + 8), (x + 22, y + 13), (x + 22, y + 23), (x + 13, y + 28), (x + 4, y + 23)], fill=CYAN)
            y = text_block(draw, x + 36, y, wrap(draw, bullet, jura(26), width - 36), jura(26), WHITE, spacing=4) + 16
        footer(draw, jura)
        cards.append((f"{n:02d}_{shot}", card))

    card = backdrop(9)  # what the Workshop mod includes
    draw = ImageDraw.Draw(card)
    text_block(draw, 60, 40, ["Included in this mod"], orbitron(44), WHITE)
    text_block(draw, 60, 102, wrap(draw, "Example emblems under Custom, backgrounds that take your empire's colours, "
                                    "and a brighter emblem picker", jura(26), W - 120), jura(26), ORANGE, spacing=4)
    for i, flag in enumerate(emblem_flags):
        card.paste(flag, (60 + i * 195, 170), flag)
    for i, flag in enumerate(background_flags):
        card.paste(flag, (155 + i * 195, 380), flag)
    footer(draw, jura, "Make your own with the free app: " + LINK)
    cards.append(("07_included", card))

    card = backdrop(10)  # how to get it working
    draw = ImageDraw.Draw(card)
    text_block(draw, 60, 40, ["How to get it working"], orbitron(44), WHITE)
    y = 130
    for i, (heading, body) in enumerate(STEPS, start=1):
        draw.ellipse([60, y, 120, y + 60], fill=CYAN)
        number = orbitron(34)
        draw.text((90 - draw.textlength(str(i), font=number) / 2, y + 9), str(i), font=number, fill=(9, 20, 24))
        text_block(draw, 145, y - 2, [heading], jura(34), ORANGE)
        text_block(draw, 145, y + 40, body.split("\n"), jura(25), WHITE, spacing=6)
        y += 140
    flag = core.compose_flag(game, studio.colourize(backgrounds[2], *pairs[0]), emblems[4], 380)
    card.paste(flag, (W - 380 - 50, (H - 380) // 2 + 20), flag)
    cards.append(("08_how_to", card))

    for name, card in cards:
        card.save(OUT / f"{name}.jpg", quality=92)
        print(f"{OUT / name}.jpg  {(OUT / f'{name}.jpg').stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
