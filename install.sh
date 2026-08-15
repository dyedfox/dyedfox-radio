#!/usr/bin/env bash
set -euo pipefail

APP=dyedfox-radio
DEST_LIB=/usr/lib/$APP
DEST_BIN=/usr/bin/$APP
DEST_DESKTOP=/usr/share/applications/$APP.desktop
DEST_ICON_PNG=/usr/share/icons/hicolor/256x256/apps/$APP.png
DEST_ICON_SVG=/usr/share/icons/hicolor/scalable/apps/$APP-tray.svg
DEST_LICENSE=/usr/share/licenses/$APP/LICENSE

SRC_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- helpers -----------------------------------------------------------------

info()  { echo "  $*"; }
warn()  { echo "  WARNING: $*" >&2; }
die()   { echo "  ERROR: $*" >&2; exit 1; }

check_cmd() { command -v "$1" &>/dev/null; }

# --- uninstall ---------------------------------------------------------------

if [[ "${1:-}" == "uninstall" ]]; then
    echo "Uninstalling $APP..."
    sudo rm -rf "$DEST_LIB"
    sudo rm -f  "$DEST_BIN" "$DEST_DESKTOP" "$DEST_ICON_PNG" "$DEST_ICON_SVG"
    sudo rm -rf "/usr/share/licenses/$APP"
    check_cmd update-desktop-database && sudo update-desktop-database /usr/share/applications 2>/dev/null || true
    check_cmd gtk-update-icon-cache   && sudo gtk-update-icon-cache -f /usr/share/icons/hicolor 2>/dev/null || true
    echo "Done."
    exit 0
fi

# --- preflight ---------------------------------------------------------------

echo "Installing $APP..."

[[ $EUID -ne 0 ]] || die "Do not run as root — the script will call sudo where needed."

check_cmd python3 || die "python3 not found."
python3 -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" \
    || die "Python 3.10 or newer required."

if check_cmd apt-get; then
    PKG_MANAGER="apt"
    # python3-pyqt6 was added in Debian 12 (Bookworm); Ubuntu has it from 22.04
    if [[ -f /etc/os-release ]]; then
        source /etc/os-release
        if [[ "${ID:-}" == "debian" && "${VERSION_ID:-0}" -lt 12 ]]; then
            die "Debian ${VERSION_ID} is not supported. Debian 12 (Bookworm) or newer is required."
        fi
    fi
elif check_cmd dnf; then
    PKG_MANAGER="dnf"
elif check_cmd pacman; then
    PKG_MANAGER="pacman"
else
    die "No supported package manager found (apt-get, dnf, or pacman). Install dependencies manually and re-run."
fi

# --- system dependencies -----------------------------------------------------

info "Installing system dependencies..."

if [[ "$PKG_MANAGER" == "apt" ]]; then
    sudo apt-get install -y \
        python3-pyqt6 \
        python3-requests \
        python3-dbus \
        python3-gi \
        gstreamer1.0-plugins-base \
        gstreamer1.0-plugins-good
elif [[ "$PKG_MANAGER" == "dnf" ]]; then
    sudo dnf install -y \
        python3-pyqt6 \
        python3-requests \
        python3-dbus \
        python3-gobject \
        gstreamer1-plugins-base \
        gstreamer1-plugins-good
elif [[ "$PKG_MANAGER" == "pacman" ]]; then
    sudo pacman -S --needed --noconfirm \
        python \
        python-pyqt6 \
        python-requests \
        python-dbus \
        python-gobject \
        gstreamer \
        gst-plugins-base \
        gst-plugins-good
fi

# --- stop a running instance --------------------------------------------------

# The app is single-instance: launching it again just raises the already-running
# window instead of restarting, so a running instance would keep serving the old
# code in memory even after the files below are replaced. Stop it so the next
# launch picks up what we're about to install.
if pgrep -f "[/ ]$APP/main\.py" >/dev/null 2>&1; then
    info "Stopping running instance..."
    pkill -f "[/ ]$APP/main\.py" || true
    sleep 1
fi

# --- copy files --------------------------------------------------------------

info "Copying application files..."
sudo rm -rf "$DEST_LIB"
sudo install -dm755 "$DEST_LIB"
sudo cp -r "$SRC_DIR"/api "$SRC_DIR"/data "$SRC_DIR"/player \
           "$SRC_DIR"/tray "$SRC_DIR"/ui "$SRC_DIR"/assets \
           "$SRC_DIR"/main.py "$DEST_LIB/"

info "Installing translations..."
sudo install -dm755 "$DEST_LIB/translations"
for qm in "$SRC_DIR"/translations/*.qm; do
    [ -f "$qm" ] && sudo install -m644 "$qm" "$DEST_LIB/translations/"
done

info "Installing icons..."
sudo install -Dm644 "$SRC_DIR/assets/icons/$APP.png"      "$DEST_ICON_PNG"
sudo install -Dm644 "$SRC_DIR/assets/icons/$APP-tray.svg" "$DEST_ICON_SVG"

info "Installing desktop entry..."
sudo install -Dm644 "$SRC_DIR/$APP.desktop" "$DEST_DESKTOP"

info "Installing license..."
sudo install -Dm644 "$SRC_DIR/LICENSE" "$DEST_LICENSE"

info "Creating launcher..."
sudo tee "$DEST_BIN" > /dev/null << 'LAUNCHER'
#!/bin/sh
exec python3 /usr/lib/dyedfox-radio/main.py "$@"
LAUNCHER
sudo chmod 755 "$DEST_BIN"

# --- post-install ------------------------------------------------------------

check_cmd update-desktop-database && sudo update-desktop-database /usr/share/applications 2>/dev/null || true
check_cmd gtk-update-icon-cache   && sudo gtk-update-icon-cache -f /usr/share/icons/hicolor 2>/dev/null || true

echo "Done. Run '$APP' to start."
echo "(If it was already running, it was stopped above — this launch starts the new build.)"
