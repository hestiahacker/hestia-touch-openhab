#!/bin/bash
# Install the HestiaPi helper scripts, sudo rule and writable state files.
# This is intentionally separate from the openHAB restore: the openHAB
# restore only knows about conf/ and userdata/.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this script as root (sudo)." >&2
  exit 1
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "$HERE/.." && pwd)"
SCRIPTS=/home/pi/scripts

if [ ! -d "$SRC/home/pi/scripts" ]; then
  echo "Missing $SRC/home/pi/scripts; extract the ZIP first." >&2
  exit 1
fi

install -d -m 0755 "$SCRIPTS"
cp -a "$SRC/home/pi/scripts/." "$SCRIPTS/"

install -m 0440 -o root -g root "$HERE/hestia-sudoers" /etc/sudoers.d/hestia
visudo -cf /etc/sudoers.d/hestia >/dev/null

# Scripts are read/executed by the openhab user but must not be writable by it.
chown -R root:root "$SCRIPTS"
chmod -R a+rX "$SCRIPTS"
chmod 0755 "$SCRIPTS"/*.sh
chmod 0644 "$SCRIPTS"/*.py

# The v2 image used chmod 777 for these files.  A narrower equivalent is to
# let openhab own the scripts directory and only the files it rewrites:
# C2F.sh/F2C.sh replace bme280.py, and exec writes the three state files.
chown openhab:openhab "$SCRIPTS"
for file in systemtype humiditymode tempunit bme280.py bme280C.py bme280F.py; do
  [ -e "$SCRIPTS/$file" ] && chown openhab:openhab "$SCRIPTS/$file"
done

systemctl enable --now mosquitto 2>/dev/null || true

echo "Installed /home/pi/scripts, /etc/sudoers.d/hestia and file permissions."
