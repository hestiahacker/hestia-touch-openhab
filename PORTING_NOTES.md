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

## GPIO

The removed local `gpio="pin:N"` binding was replaced by one
`exec:command` thing per pin. A link transform converts the Pin Switch
ON/OFF command to `1`/`0`, and `hestia-setgpio.sh` writes the sysfs GPIO.
`autoupdate` metadata preserves the v2 Pin Switch state behavior.

## System scripts

- `python` invocations are now `python3`.
- openHAB paths are `/etc/openhab` and `/var/lib/openhab`.
- Privileged helper scripts are run through a narrow
  `/etc/sudoers.d/hestia` rule.
- `updateone.sh` no longer pulls the v2 tree; restore the v5 ZIP instead.

## Build

Run `python3 tools/build_v5.py` to regenerate `v5/` and
`v5/hestia-openhab-v5.zip` deterministically.
