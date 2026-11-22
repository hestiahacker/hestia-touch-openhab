#!/bin/bash
# HestiaPi GPIO output helper for openHAB 5.
#
# The HestiaPi rules keep the Raspberry Pi BCM pin numbers (12, 16, 18, 20,
# 23) so the same rules work on every board.  This helper translates a
# logical pin to the Linux global GPIO number for the board it is running on.
# Usage: hestia-setgpio.sh <logical-pin> <0|1>
#
# Environment:
#   HESTIA_GPIO_BOARD=auto|rpi|orangepi-zero2w|generic
#   HESTIA_GPIO_ROOT=/sys/class/gpio
#   HESTIA_GPIO_DRY_RUN=1  print the resolved GPIO and exit
set -u

PIN="${1:?pin number required}"
VALUE="${2:?value 0 or 1 required}"
GPIO_ROOT="${HESTIA_GPIO_ROOT:-/sys/class/gpio}"
DEFAULT_BOARD="auto"
BOARD="${HESTIA_GPIO_BOARD:-$DEFAULT_BOARD}"

case "$VALUE" in
  0|1) ;;
  *) echo "value must be 0 or 1" >&2; exit 2 ;;
esac

detect_board() {
  local compatible=""
  local model=""
  if [ -r /proc/device-tree/compatible ]; then
    compatible="$(tr '\0' '\n' < /proc/device-tree/compatible)"
  fi
  if [ -r /proc/device-tree/model ]; then
    model="$(tr -d '\0' < /proc/device-tree/model)"
  fi
  case "$compatible $model" in
    *raspberrypi*|*"Raspberry Pi"*) echo rpi ;;
    *xunlong,orangepi-zero2w*|*"OrangePi Zero 2W"*) echo orangepi-zero2w ;;
    *allwinner,sun50i-h618*) echo orangepi-zero2w ;;
    *) echo generic ;;
  esac
}

if [ "$BOARD" = "auto" ]; then
  BOARD="$(detect_board)"
fi

case "$BOARD" in
  rpi|generic)
    # Raspberry Pi sysfs numbering is the BCM number.  "generic" is the
    # same 1:1 mapping for boards that expose the Linux GPIO numbers
    # directly.
    GPIO="$PIN"
    ;;
  orangepi|orangepi-zero2w|opi)
    BOARD="orangepi-zero2w"
    # Orange Pi Zero 2W (Allwinner H618) 40-pin header.  The HestiaPi PCB
    # uses the same physical pins as on the Raspberry Pi:
    #   physical 12 = PI1  = 257   (logical BCM 18)
    #   physical 16 = PI14 = 270   (logical BCM 23)
    #   physical 32 = PI11 = 267   (logical BCM 12)
    #   physical 36 = PC12 = 76    (logical BCM 16)
    #   physical 38 = PI4  = 260   (logical BCM 20)
    case "$PIN" in
      12) GPIO=267 ;;
      16) GPIO=76 ;;
      18) GPIO=257 ;;
      20) GPIO=260 ;;
      23) GPIO=270 ;;
      *)
        echo "unsupported logical GPIO $PIN for $BOARD" >&2
        exit 2
        ;;
    esac
    ;;
  *)
    echo "unknown GPIO board '$BOARD' (use auto, rpi, orangepi-zero2w or generic)" >&2
    exit 2
    ;;
esac

if [ "${HESTIA_GPIO_DRY_RUN:-0}" = "1" ]; then
  echo "board=$BOARD logical=$PIN gpio=$GPIO value=$VALUE"
  exit 0
fi

if [ ! -d "$GPIO_ROOT" ]; then
  echo "GPIO sysfs is not available at $GPIO_ROOT" >&2
  exit 1
fi

if [ ! -d "$GPIO_ROOT/gpio$GPIO" ]; then
  echo "$GPIO" > "$GPIO_ROOT/export" || {
    echo "cannot export GPIO $GPIO (logical $PIN on $BOARD)" >&2
    exit 1
  }
fi

if ! echo out > "$GPIO_ROOT/gpio$GPIO/direction" 2>/dev/null; then
  echo "cannot set GPIO $GPIO direction to out" >&2
  exit 1
fi

echo "$VALUE" > "$GPIO_ROOT/gpio$GPIO/value"
