#!/bin/bash
# HestiaPi GPIO output helper for openHAB 5 (sysfs, same pins as the v2
# gpio binding).  Usage: hestia-setgpio.sh <bcm-pin> <0|1>
set -u

PIN="${1:?pin number required}"
VALUE="${2:?value 0 or 1 required}"
GPIO_ROOT="${HESTIA_GPIO_ROOT:-/sys/class/gpio}"

case "$VALUE" in
  0|1) ;;
  *) echo "value must be 0 or 1" >&2; exit 2 ;;
esac

if [ ! -d "$GPIO_ROOT" ]; then
  echo "GPIO sysfs is not available at $GPIO_ROOT" >&2
  exit 1
fi

if [ ! -d "$GPIO_ROOT/gpio$PIN" ]; then
  echo "$PIN" > "$GPIO_ROOT/export" || {
    echo "cannot export GPIO $PIN" >&2
    exit 1
  }
fi

echo out > "$GPIO_ROOT/gpio$PIN/direction" 2>/dev/null || true
echo "$VALUE" > "$GPIO_ROOT/gpio$PIN/value"
