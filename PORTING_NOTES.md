# openHAB 2 to 5 porting notes

The rules and item names are kept as close to the v2 originals as possible.
Only v2 interfaces that no longer exist in openHAB 5 were changed.

## Storage changes

- JSONDB classes and field names were updated from the openHAB 2
  `org.eclipse.smarthome.*` schema to the openHAB 5 `org.openhab.core.*`
  schema.
- Thing channels use the v5 string `uid` / `channelTypeUID` form and a flat
  `configuration` map.
- Exec `transform` values are lists in v5.
- MQTT channel type UIDs use the v5 colon form (`mqtt:string`,
  `mqtt:publishTrigger`, ...). The obsolete v2 broker option
  `retainMessages` was removed.
- The v2 `TempSetpoint` item, MQTT channel and rule trigger are dead
  references in the source JSONDB; they are omitted so v5 can load the
  configuration cleanly.
- The v2 persistence file used the `default = everyChange` strategy. openHAB
  5.1 removed default strategies, so the file now names its strategies on
  every `Items` line. The unused every-minute strategy is kept from v2.
- The v5 Exec things use `REGEX((.*))`; the Regex Transformation add-on is
  therefore listed in `addons.cfg`.
- `conf/items`, `conf/things` and `conf/scripts` contain the v2 placeholder
  files so openHAB does not warn about missing model folders.

## Script changes

- Every UI rule script explicitly uses
  `application/javascript;version=ECMAScript-5.1`, which selects the Nashorn
  engine in openHAB 5. Without the version suffix, openHAB 5 selects GraalJS.
- `utils.js` was updated for the v5 script API (timers, sleep,
  `ExecUtil.executeCommandLineAndWaitResponse`, logging). A small `logger`
  compatibility shim preserves the one v2 rule that used `logger.warn`.
- v5 `items["Name"]` returns a State, not an Item, so group iteration uses
  `ir.getItem("Group").members`.
- v5 `events.sendCommand` needs the item name, not an Item object.
- Java Streams require `.count()`, not the v2 collection-style `.size`
  property.
- The empty v2 `HumidityBoostTime` command trigger became the equivalent v5
  state-change trigger.
- v5 does not expose the rule `event` inside timer callbacks. The two scripts
  that used it after a delay (`Synchronize Temp Proxies` and the `Boost`
  countdown) copy the needed event fields into local variables first.
- The Nashorn add-on is installed while openHAB starts. On the first start
  after a restore the `System started` rule can therefore run before the
  ECMAScript engine exists. `automation/jsr223/hestia_startup.nashornjs` only
  loads once Nashorn is ready and kicks off initialization there. The DSL
  rule keeps a 60-second fallback for systems where that script cannot load.
- The heat-pump guard in `Heating/Cooling Check` required
  `CoolingPin == OFF` before sending `HeatingCtrl OFF`. The v2 GPIO binding
  reported unused pin items as OFF; the v5 Exec replacement leaves them
  `NULL`, which blocked stage 1 heating from ever turning off in HP mode.
  The guard now blocks only while `CoolingPin == ON`, preserving the shared
  stage-1 protection and working on a fresh install.

## GPIO

The removed local `gpio="pin:N"` binding was replaced by one
`exec:command` thing per pin. A link transform converts the Pin Switch
ON/OFF command to `1`/`0`, and `hestia-setgpio.sh` writes the sysfs GPIO.
`autoupdate` metadata preserves the v2 Pin Switch state behavior.

The rules keep the Raspberry Pi BCM pin numbers (`12`, `16`, `18`, `20`,
`23`). `hestia-setgpio.sh` translates them to the Linux global GPIO number
for the board it is running on, so the same rules and ZIP work on both
boards:

| Logical (RPi BCM) | RPi physical pin | Orange Pi Zero 2W | Linux GPIO |
| --- | --- | --- | --- |
| 12 | 32 | PI11 | 267 |
| 16 | 36 | PC12 | 76 |
| 18 | 12 | PI1 | 257 |
| 20 | 38 | PI4 | 260 |
| 23 | 16 | PI14 | 270 |

Board detection uses `/proc/device-tree/compatible`; `HESTIA_GPIO_BOARD`
can force `rpi`, `orangepi-zero2w` or `generic` at runtime.
`python3 tools/build_v5.py --gpio-board ...` only changes the default baked
into the helper; the default `auto` produces one universal ZIP. The
`pinNN_on.sh` / `pinNN_off.sh` scripts are thin wrappers around the helper,
so the v2 Exec things and rules do not need board-specific copies.

## LCD kiosk and OneUI

- The custom OneUI build (`home/pi/scripts/oneui`) is installed to
  `/usr/local/oneui` by `setup/install.sh` and made world-readable.  Chromium
  loads it directly with `file:///usr/local/oneui/index.html`; no web server
  is required.
- `setup/kiosk.service` and `setup/kiosk-xinitrc` replace the image-specific
  kiosk session.  The service runs as `user`, and the installer enables it
  only when that account, `startx` and `chromium` are available.
- The Xorg `fbdev` configuration for the ILI9486 LCD is installed only when
  `/dev/fb1` exists, so the same ZIP is safe on a board without the panel.
- OneUI talks to Mosquitto over MQTT/WebSocket on port `9001`.  The installer
  adds a local-only WebSocket listener and leaves the openHAB MQTT binding on
  port `1883`.
- The BME280 scripts now probe I2C buses 1, 2 and 0 on addresses `0x76` and
  `0x77`.  Bookworm exposes the HestiaPi sensor on `i2c-2`; the v2 scripts
  assumed `i2c-1`.

## System scripts

- `python` invocations are now `python3`.
- openHAB paths are `/etc/openhab` and `/var/lib/openhab`.
- Privileged helper scripts are run through a narrow
  `/etc/sudoers.d/hestia` rule.
- `updateone.sh` no longer pulls the v2 tree; restore the v5 ZIP instead.
- `getssid.sh` and `getwifiinfo.sh` use `iw` because Bookworm no longer ships
  `iwconfig`.
- `setup/install.sh` adds the `openhab` user to the `i2c` group so the Exec
  binding can read `/dev/i2c-2` as the unprivileged openhab user.

## Build

Run `python3 tools/build_v5.py` to regenerate `v5/` and
`v5/hestia-openhab-v5.zip` deterministically. The optional
`--gpio-board auto|rpi|orangepi-zero2w|generic` flag changes only the
default baked into `hestia-setgpio.sh`; `auto` (the default) produces one
ZIP that detects the board at runtime.
