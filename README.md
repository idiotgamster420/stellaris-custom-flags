# Stellaris Flag Uploader

A desktop app that makes and manages the **Custom Flags** mod for Stellaris:
your own images as emblems, exact custom colours, a Black Ops 2-style layer
editor for backgrounds and emblems, emblems that fill the whole flag, and a
designer that previews flags the way the game draws them.

On first launch it creates the mod in the Stellaris mod folder and registers it
with the Paradox launcher; enable "Custom Flags" in a playset to use it.

## No game files included

Everything that comes from Stellaris (UI textures, fonts, the flag frame, and
the parts of the mod based on the game's own shader and interface files) is
read from the player's installed game at run time. Nothing from Paradox is in
this repository or in the builds.

## Layout

- `app/flag_uploader.py`: the app and the mod logic (start here)
- `app/flag_studio.py`: the Designer and Maker tabs
- `app/icon.png`: app icon (drawn by `packaging/make_icon.py`)
- `packaging/linux/`: AppImage build script, launcher, desktop entry

## Run from source (Linux)

Needs Python 3 with PyGObject (GTK 3), pycairo and Pillow
(`python3-gi python3-gi-cairo gir1.2-gtk-3.0 python3-pil` on Debian/Ubuntu/Mint):

    python3 app/flag_uploader.py            # the app
    python3 app/flag_uploader.py --check    # where it finds the game and the mod

Set `CUSTOM_FLAGS_MOD=/some/folder` to work on a test mod instead of the real one.

## Build the Linux AppImage

    packaging/linux/build-appimage.sh

Downloads linuxdeploy and its GTK plugin into `build/tools` the first time, then
writes `build/Stellaris_Flag_Uploader-x86_64.AppImage` (about 44 MB). It bundles
the build machine's Python, GTK and libraries, so it runs on systems with the
same or newer glibc. Built on Linux Mint 22 it needs glibc 2.38+ (Ubuntu 24.04,
Mint 22, Fedora 39, Debian 13, Arch or newer). Build on an older distro for
wider support.

The AppImage adds itself to the app menu when first run.

## Windows

Planned. The app already knows the Windows Documents and Steam locations; the
build (PyInstaller with MSYS2's GTK, plus an installer) still needs setting up.
