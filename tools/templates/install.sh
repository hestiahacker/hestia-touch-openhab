#!/bin/bash
# Install the HestiaPi helper scripts, OneUI kiosk and MQTT WebSocket
# listener.  This is intentionally separate from the openHAB restore: the
# openHAB restore only knows about conf/ and userdata/.
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this script as root (sudo)." >&2
  exit 1
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "$HERE/.." && pwd)"
SCRIPTS=/home/pi/scripts
UI=/usr/local/oneui

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
# Make sure openhab can see into the user's home directory
chgrp openhab /home/pi
chmod g+rx /home/pi

# The Exec binding runs the BME280 scripts as the openhab user.  Without the
# i2c group it cannot open /dev/i2c-*, and the sensor readings fail.
if getent passwd openhab >/dev/null && getent group i2c >/dev/null; then
  usermod -aG i2c openhab
fi

# Install the custom OneUI build where it does not depend on a checkout and
# is readable by the non-root kiosk user.
if [ -f "$SCRIPTS/oneui/index.html" ]; then
  install -d -m 0755 "$UI"
  cp -a "$SCRIPTS/oneui/." "$UI/"
  install -m 0755 "$HERE/kiosk-xinitrc" "$UI/kiosk-xinitrc"
  chown -R root:root "$UI"
  chmod -R a+rX "$UI"
else
  echo "Warning: OneUI assets not found in $SCRIPTS/oneui" >&2
fi

# OneUI connects to MQTT over WebSocket on localhost:9001.  The normal
# 1883 listener stays available for the openHAB MQTT binding.
if command -v mosquitto >/dev/null 2>&1; then
  install -m 0644 "$HERE/mosquitto-oneui.conf" \
    /etc/mosquitto/conf.d/oneui-websocket.conf
  systemctl enable mosquitto >/dev/null 2>&1 || true
  systemctl restart mosquitto
  echo "Configured MQTT over WebSocket on 127.0.0.1:9001."
else
  echo "Mosquitto is not installed; skipped the WebSocket listener."
fi

# The kiosk is optional: install it only where its user and software exist.
if getent passwd pi >/dev/null \
  && command -v startx >/dev/null 2>&1 \
  && command -v chromium >/dev/null 2>&1; then
  if [ -e /dev/fb1 ]; then
    install -d -m 0755 /etc/X11/xorg.conf.d
    install -m 0644 "$HERE/xorg-20-kiosk.conf" \
      /etc/X11/xorg.conf.d/20-kiosk.conf
  fi
  install -m 0644 "$HERE/kiosk.service" /etc/systemd/system/kiosk.service
  systemctl daemon-reload
  systemctl enable kiosk.service
  echo "Installed and enabled kiosk.service."
else
  echo "Kiosk prerequisites not found; installed OneUI without starting it."
fi

echo "Installed scripts, OneUI and service configuration."
