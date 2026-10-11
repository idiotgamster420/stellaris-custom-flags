#!/usr/bin/python3
"""Flag pack checks: a round trip gives byte-identical game files, a second import changes
nothing, and broken packs are refused before anything is written. Needs Stellaris installed.

  python3 tests/test_packs.py
"""
import importlib.util
import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

from PIL import Image

APP = Path(__file__).resolve().parents[1] / "app" / "flag_uploader.py"


def core(mod):
    os.environ["CUSTOM_FLAGS_MOD"] = str(mod)
    spec = importlib.util.spec_from_file_location(f"core_{mod.name}", APP)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.ensure_mod(m.find_game())
    return m


def files(mod):
    return {p.relative_to(mod): p.read_bytes() for p in sorted(mod.rglob("*"))
            if p.is_file() and p.parts[len(mod.parts)] in ("flags", "uploads", "maker")}


def png(size=(40, 30), colour=(200, 40, 40, 255)):
    buf = io.BytesIO()
    Image.new("RGBA", size, colour).save(buf, "PNG")
    return buf.getvalue()


def main():
    tmp = Path(tempfile.mkdtemp())
    a = core(tmp / "a")
    if not a.find_game():
        sys.exit("Stellaris isn't installed; these checks need it")
    (tmp / "pic.png").write_bytes(png())
    a.update(a.upload(tmp / "pic.png"), full=True)
    a.upload(tmp / "pic.png", fit=True)
    a.save_colours([{"key": a.new_colour_key([]), "name": "Teal", "rgb": [0, 128, 128]}])
    assert a.export_pack(tmp / "pack.zip", "Test") == {"emblems": 2, "backgrounds": 0, "colours": 1, "designs": 0}

    b = core(tmp / "b")
    pack, contents = b.read_pack(tmp / "pack.zip")
    b.import_pack(pack, contents, b.pack_plan(pack, contents), b.find_game())
    assert files(tmp / "a") == files(tmp / "b"), "imported files differ"
    assert b.load_colours() == a.load_colours()
    again = b.pack_plan(*b.read_pack(tmp / "pack.zip"))
    assert not any(again[kind][what] for kind in b.PACK_KINDS for what in ("add", "replace")), "re-import changed things"
    assert b.peek_pack(tmp / "pack.zip")["name"] == "Test"

    broken = {
        "traversal": ({"format": 1, "emblems": {"../x": {}}}, {}),
        "not an image": ({"format": 1, "emblems": {"x": {}}}, {"emblems/x.png": b"nope"}),
        "bad colour": ({"format": 1, "colours": [{"key": "cf_x", "name": "x", "rgb": [300, 0, 0]}]}, {}),
        "bad design": ({"format": 1, "designs": ["emblem_x.json"]}, {"designs/emblem_x.json": json.dumps({"kind": "emblem"})}),
        "newer format": ({"format": 99}, {}),
    }
    c = core(tmp / "c")
    before = files(tmp / "c")
    for name, (meta, members) in broken.items():
        path = tmp / f"{name}.zip"
        with zipfile.ZipFile(path, "w") as z:
            z.writestr("pack.json", json.dumps(meta))
            for member, data in members.items():
                z.writestr(member, data)
        try:
            c.read_pack(path)
            raise AssertionError(f"{name}: accepted a broken pack")
        except ValueError:
            pass
    assert files(tmp / "c") == before, "a broken pack wrote files"
    assert c.new_pack_path("CON").stem == "CON pack"
    print("flag packs ok")


if __name__ == "__main__":
    main()
