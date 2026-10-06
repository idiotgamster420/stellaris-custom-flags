# PyInstaller recipe for the Windows build. Run from the repository root in an MSYS2
# UCRT64 shell with python, python-gobject, python-cairo, python-pillow, gtk3,
# adwaita-icon-theme and pyinstaller installed (see .github/workflows/windows.yml):
#   pyinstaller --noconfirm --distpath build/windows/dist --workpath build/windows/work packaging/windows/stellaris-flag-uploader.spec
import os

root = os.path.abspath(os.path.join(SPECPATH, "..", ".."))
app = os.path.join(root, "app")

a = Analysis(
    [os.path.join(app, "flag_uploader.py")],
    pathex=[app],
    datas=[(os.path.join(app, "icon.png"), ".")],
    hiddenimports=["flag_studio", "cairo", "gi._gi_cairo", "gi.repository.Gtk", "gi.repository.Gdk",
                   "gi.repository.GdkPixbuf", "gi.repository.GLib", "gi.repository.Gio",
                   "gi.repository.Pango", "gi.repository.PangoCairo"],
    hooksconfig={"gi": {"module-versions": {"Gtk": "3.0"}, "icons": ["Adwaita"], "themes": ["Adwaita"],
                        "languages": ["en_US", "en_GB"]}},
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="StellarisFlagUploader",
          icon=os.path.join(app, "icon.ico"), console=False)
coll = COLLECT(exe, a.binaries, a.datas, name="StellarisFlagUploader")
