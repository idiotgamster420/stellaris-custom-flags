#!/usr/bin/env bash
# Builds build/Stellaris_Flag_Uploader-x86_64.AppImage: the app plus this system's
# Python 3, PyGObject/GTK 3, pycairo and Pillow, so it runs with nothing installed.
# The AppImage needs this system's glibc version or newer, so build on the oldest
# distro you want to support. Needs: python3, python3-gi, python3-gi-cairo,
# gir1.2-gtk-3.0, python3-pil, rsync, curl.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUILD="$ROOT/build"
TOOLS="$BUILD/tools"
APPDIR="$BUILD/AppDir"
LIBS=/usr/lib/x86_64-linux-gnu
PY="$(readlink -f /usr/bin/python3)"
PYVER="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"

mkdir -p "$TOOLS"
fetch() {
    [ -x "$TOOLS/$1" ] || { curl -fsSL -o "$TOOLS/$1" "$2" && chmod +x "$TOOLS/$1"; }
}
fetch linuxdeploy-x86_64.AppImage https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage
fetch linuxdeploy-plugin-gtk.sh https://raw.githubusercontent.com/linuxdeploy/linuxdeploy-plugin-gtk/master/linuxdeploy-plugin-gtk.sh

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/lib/python3/dist-packages" "$APPDIR/usr/share/stellaris-flag-uploader"
cp "$PY" "$APPDIR/usr/bin/python3"
# The standard library, minus parts the app never uses.
rsync -a --exclude __pycache__ --exclude test --exclude tests --exclude idlelib --exclude tkinter \
    --exclude turtledemo --exclude ensurepip --exclude lib2to3 --exclude pydoc_data --exclude 'config-*' \
    --exclude dist-packages --exclude site-packages "/usr/lib/python$PYVER/" "$APPDIR/usr/lib/python$PYVER/"
for package in gi cairo PIL; do
    cp -a "/usr/lib/python3/dist-packages/$package" "$APPDIR/usr/lib/python3/dist-packages/"
done
find "$APPDIR" -name __pycache__ -prune -exec rm -rf {} +
cp "$ROOT"/app/*.py "$ROOT/app/icon.png" "$APPDIR/usr/share/stellaris-flag-uploader/"
install -m 755 "$ROOT/packaging/linux/stellaris-flag-uploader" "$APPDIR/usr/bin/"
cp "$ROOT/app/icon.png" "$BUILD/stellaris-flag-uploader.png"

# linuxdeploy bundles what these need. The GTK libraries are loaded by name at run time
# (through GObject introspection), so they're listed explicitly.
args=(--appdir "$APPDIR" --executable "$APPDIR/usr/bin/python3")
while IFS= read -r module; do
    args+=(--deploy-deps-only "$module")
done < <(find "$APPDIR/usr/lib/python$PYVER/lib-dynload" "$APPDIR/usr/lib/python3/dist-packages" -name '*.so')
for lib in libgtk-3.so.0 libgdk-3.so.0 libgdk_pixbuf-2.0.so.0 libpango-1.0.so.0 libpangocairo-1.0.so.0 \
           libpangoft2-1.0.so.0 libatk-1.0.so.0 libcairo-gobject.so.2 libharfbuzz-gobject.so.0 \
           libgirepository-1.0.so.1 libgio-2.0.so.0 libgmodule-2.0.so.0 librsvg-2.so.2; do
    [ -e "$LIBS/$lib" ] && args+=(--library "$LIBS/$lib")
done

cd "$BUILD"
export DEPLOY_GTK_VERSION=3 ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 LDAI_OUTPUT=Stellaris_Flag_Uploader-x86_64.AppImage
PATH="$TOOLS:$PATH" "$TOOLS/linuxdeploy-x86_64.AppImage" "${args[@]}" \
    --desktop-file "$ROOT/packaging/linux/stellaris-flag-uploader.desktop" \
    --icon-file "$BUILD/stellaris-flag-uploader.png" \
    --custom-apprun "$ROOT/packaging/linux/AppRun" \
    --plugin gtk --output appimage
echo "Built $BUILD/Stellaris_Flag_Uploader-x86_64.AppImage"
