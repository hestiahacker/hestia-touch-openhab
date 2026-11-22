# Add-ons and software

Tested with openHAB 5.2.4 on Debian 13.

## openHAB add-ons

`conf/services/addons.cfg` installs exactly these add-ons on every start:

| Add-on | Add-on ID |
| --- | --- |
| JavaScript Scripting (Nashorn) | `automation-jsscriptingnashorn` |
| MQTT Binding | `binding-mqtt` |
| Exec Binding | `binding-exec` |
| MapDB Persistence | `persistence-mapdb` |
| Regex Transformation | `transformation-regex` |
| Basic UI | `ui-basic` |

The table lists the full add-on UIDs shown in Main UI; `addons.cfg` uses the
short name after the add-on type (for example `binding = mqtt,exec`).

The generated `addons.cfg` is:

```
automation = jsscriptingnashorn
binding = mqtt,exec
persistence = mapdb
transformation = regex
ui = basic
```

openHAB Cloud is optional and is not required by the rules. To use myopenHAB,
add `misc = openhabcloud` to `addons.cfg`.

The Exec things use `REGEX((.*))` to keep only the command output; this
requires the Regex Transformation add-on.

The v2 GPIO binding is not used. The GPIO outputs are driven by the Exec
Binding and `/home/pi/scripts/hestia-setgpio.sh` (Linux sysfs). The helper
maps the Raspberry Pi BCM pin numbers used by the rules to the actual Linux
GPIO numbers, so the same configuration works on a Raspberry Pi Zero 2 W or
an Orange Pi Zero 2W; no `raspi-gpio` or `gpiod` package is required.

## Operating-system packages

Required by the rules and helper scripts:

```
mosquitto mosquitto-clients
python3 python3-smbus i2c-tools
zip unzip
curl wget iputils-ping
sudo
```

- `mosquitto` must be running on `localhost:1883`. The MQTT broker thing uses
  no credentials; the Debian default (localhost-only listener) permits this.
- `python3`, `python3-smbus` and `i2c-tools` are used for the BME280 sensor.
  Enable I2C on the board and verify the sensor with `i2cdetect -y 1` (or the
  bus used by the board; the supplied `bme280.py` uses bus 2).
- `zip` and `unzip` are required by `openhab-cli backup` / `openhab-cli
  restore`.
- `curl`, `wget` and `ping` are used by the version, public-IP and network
  helper scripts.

Optional HestiaPi LCD/kiosk software (not needed for the openHAB rules):

```
xinit xserver-xorg xserver-xorg-video-fbdev x11-xserver-utils
matchbox-window-manager unclutter xwit fbset
chromium (or kweb)
net-tools wireless-tools lsb-release bc
```

On a Raspberry Pi image, `raspi-config` is also used by `kiosk-xinit.sh`.
The kiosk service itself is image-specific and is not installed by this ZIP.
