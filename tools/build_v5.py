#!/usr/bin/env python3
"""Build an openHAB 5 configuration tree from the HestiaPi openHAB 2 tree.

The converter is intentionally small and deterministic:
  * reads the v2 text configuration and JSONDB from this repository;
  * keeps every rule body and item definition as close to v2 as possible;
  * only changes storage schemas and the v2 interfaces that no longer exist
    in v5 (see PORTING_NOTES.md);
  * writes a complete conf/ + userdata/jsondb/ tree under v5/.

Run from anywhere:  python3 tools/build_v5.py [--gpio-board auto|rpi|orangepi-zero2w|generic]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
V2CONF = ROOT / "etc" / "openhab2"
V2DB = ROOT / "var" / "lib" / "openhab2" / "jsondb"
SCRIPTS = ROOT / "home" / "pi" / "scripts"
TEMPLATES = ROOT / "tools" / "templates"

OUT = ROOT / "v5"
CONF = OUT / "conf"
DB = OUT / "userdata" / "jsondb"
OUT_SCRIPTS = OUT / "home" / "pi" / "scripts"
SETUP = OUT / "setup"

# Logical HestiaPi GPIO numbers (Raspberry Pi BCM numbering).  The helper
# translates these to the Linux GPIO numbers used by the actual board.
PIN_IDS = (12, 16, 18, 20, 23)
GPIO_BOARD_DEFAULT = "auto"
SCRIPT_TYPE = "application/javascript;version=ECMAScript-5.1"

ITEM_CLASS = "org.openhab.core.items.ManagedItemProvider$PersistedItem"
METADATA_CLASS = "org.openhab.core.items.Metadata"
THING_CLASS = "org.openhab.core.thing.internal.ThingStorageEntity"
LINK_CLASS = "org.openhab.core.thing.link.ItemChannelLink"
RULE_CLASS = "org.openhab.core.automation.dto.RuleDTO"

# The v5 exec binding standard channel set.
EXEC_CHANNELS = (
    ("output", "exec:output", "String", "STATE", "Output",
     "Output of the last execution of the command"),
    ("stdout", "exec:stdout", "String", "STATE", "Standard Out",
     "The stdout output of the last execution of the command"),
    ("stderr", "exec:stderr", "String", "STATE", "Standard Error",
     "The stderr output of the last execution of the command"),
    ("input", "exec:input", "String", "STATE", "Input",
     "Input that will be passed as second parameter to the command"),
    ("exit", "exec:exit", "Number", "STATE", "Exit Value",
     "The exit value of the last execution of the command"),
    ("run", "exec:run", "Switch", "STATE", "Running",
     "Send ON to execute the command and the current state tells whether it is running or not"),
    ("lastexecution", "exec:lastexecution", "DateTime", "STATE", "Last Execution",
     "Time/Date the command was last executed, in yyyy-MM-dd'T'HH:mm:ss.SSSZ format"),
)

# Commands that need to run as root.  The exec thing configuration is changed
# to use sudo; /etc/sudoers.d/hestia is generated with the matching script list.
ROOT_SCRIPTS = (
    "hestia-setgpio.sh",
    "settz.sh",
    "getgitlatestversion.sh",
    "backup.sh",
    "restore.sh",
    "reboot.sh",
    "shutdown.sh",
)

DEFAULT_RULES = """\
rule "Kick off initialization"
when
  System started
then
  var logName = "initialization.kickoff"
  logInfo(logName, "OH is loaded, kicking off initialization, Initialized == " + Initialized.state)
  if(Initialized.state == ON) logWarn(logName, "Already initialized!")
  else if(Initialized.state == OFF) logWarn(logName, "Already initializing")
  else {
    // On the first start after a restore openHAB may run this rule before
    // the Nashorn add-on has been installed.  The jsr223 file script below
    // starts initialization as soon as Nashorn is available; this timer is
    // only the fallback for a system where that script could not be loaded.
    createTimer(now.plusSeconds(60), [ |
      if(Initialized.state == NULL) {
        logInfo(logName, "Commanding Initialization Rule to start")
        StartInitialization.sendCommand(ON)
      }
    ])
  }
end
"""

STARTUP_SCRIPT = """\
// openHAB 5 installs the Nashorn add-on while it is starting, so the first
// "System started" event can arrive before this script engine exists.  File
// scripts are only loaded after their engine is available, which makes this
// the deterministic place to kick off initialization.
var OPENHAB_CONF = Java.type('java.lang.System').getenv('OPENHAB_CONF');
load(OPENHAB_CONF + '/automation/lib/hestia/utils.js');

var logName = "initialization.kickoff";
var attempts = 0;

var kick = function() {
  if (items["Initialized"] == ON || items["Initialized"] == OFF) {
    logDebug(logName, "Initialization already handled, Initialized == " + items["Initialized"]);
    return;
  }
  logInfo(logName, "Nashorn is ready, commanding Initialization Rule to start");
  events.sendCommand("StartInitialization", ON);
  attempts++;
  if (attempts < 12) {
    createTimerSecs(5, kick);
  }
};

createTimerSecs(2, kick);
"""

SETGPIO = """\
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
    compatible="$(tr '\\0' '\\n' < /proc/device-tree/compatible)"
  fi
  if [ -r /proc/device-tree/model ]; then
    model="$(tr -d '\\0' < /proc/device-tree/model)"
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
"""

SUDOERS = "openhab ALL=(root) NOPASSWD: " + ", ".join(
    f"/home/pi/scripts/{name}" for name in ROOT_SCRIPTS
) + "\n"

ADDONS_CFG = """\
# HestiaPi openHAB 5 add-ons.
# This list is applied at every start; add-ons not listed are removed.
automation = jsscriptingnashorn
binding = mqtt,exec
persistence = mapdb
transformation = regex
ui = basic

# Optional remote access add-on (not required by the rules):
#misc = openhabcloud
"""

# openHAB 5.1 removed the "default" strategy.  Every Items line therefore has
# to name its strategies explicitly.  The v2 file also persisted every item
# every minute, which no rule in this configuration uses.
MAPDB_PERSIST = """\
Strategies {
  everyMinute  : "0 */1 * * * ?"
}
Items {
        RestoreOnStartup* : strategy = everyUpdate, restoreOnStartup
}
"""

# The v2 folders are kept so openHAB does not warn about missing model
# folders.  The items/things are managed in JSONDB; the files are placeholders.
CONF_PLACEHOLDERS = {
    "items/README.md": "This is where items go\n",
    "things/README.md": "This is where things go\n",
    "scripts/readme.txt": "This is where scripts go\n",
}


def load_json(path: Path):
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def write_text(path: Path, text: str, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(mode)


def colon(segments) -> str:
    return ":".join(segments)


def flat_config(config) -> dict:
    """v2 stores config as {properties: {...}}; v5 things use a flat map."""
    if isinstance(config, dict) and set(config) == {"properties"}:
        return dict(config["properties"])
    return dict(config or {})


def script_type(module_type: str) -> str:
    if module_type == "application/javascript":
        return SCRIPT_TYPE
    return module_type


def exec_channels(thing_uid: str) -> list:
    channels = []
    for cid, channel_type, item_type, kind, label, description in EXEC_CHANNELS:
        channel = {
            "uid": f"{thing_uid}:{cid}",
            "id": cid,
            "channelTypeUID": channel_type,
            "itemType": item_type,
            "kind": kind,
            "label": label,
            "description": description,
            "defaultTags": [],
            "properties": {},
            "configuration": {},
        }
        channels.append(channel)
    return channels


# ---------------------------------------------------------------------------
# JSONDB conversion
# ---------------------------------------------------------------------------

def build_items() -> None:
    source = load_json(V2DB / "org.eclipse.smarthome.core.items.Item.json")
    items = {}
    for name in sorted(source):
        items[name] = {"class": ITEM_CLASS, "value": source[name]["value"]}

    # The v2 GPIO switch items are moved into JSONDB so the links below are
    # restored atomically with the items.  Pin numbers stay in Raspberry Pi
    # BCM numbering; hestia-setgpio.sh translates them for the actual board.
    for pin in PIN_IDS:
        items[f"Pin{pin}"] = {
            "class": ITEM_CLASS,
            "value": {
                "groupNames": ["Pins"],
                "itemType": "Switch",
                "tags": [],
            },
        }
    write_json(DB / "org.openhab.core.items.Item.json", items)


def build_metadata() -> None:
    source = load_json(V2DB / "org.eclipse.smarthome.core.items.Metadata.json")
    metadata = {}
    for key in sorted(source):
        value = dict(source[key]["value"])
        segments = value["key"]["segments"]
        value["key"] = {"segments": segments, "uid": colon(segments)}
        metadata[key] = {"class": METADATA_CLASS, "value": value}

    # A v5 transform-profile link does not feed the item state back, so the
    # GPIO switch items need autoupdate to behave like the v2 gpio items.
    for pin in PIN_IDS:
        key = f"autoupdate:Pin{pin}"
        metadata[key] = {
            "class": METADATA_CLASS,
            "value": {
                "key": {"segments": ["autoupdate", f"Pin{pin}"], "uid": key},
                "value": "true",
                "configuration": {},
            },
        }
    write_json(DB / "org.openhab.core.items.Metadata.json", metadata)


def convert_channel(channel: dict) -> dict:
    segments = channel["uid"]["segments"]
    uid = colon(segments)
    channel_type = colon(channel["channelTypeUID"]["segments"])
    kind = channel.get("kind", "STATE")

    converted = {
        "uid": uid,
        "id": segments[-1],
        "channelTypeUID": channel_type,
    }
    if kind != "TRIGGER":
        item_type = channel.get("acceptedItemType")
        if item_type and item_type != "None":
            converted["itemType"] = item_type
    converted["kind"] = kind
    converted["label"] = channel.get("label", "")
    if channel.get("description"):
        converted["description"] = channel["description"]
    converted["defaultTags"] = channel.get("defaultTags", [])
    converted["properties"] = channel.get("properties", {})
    converted["configuration"] = flat_config(channel.get("configuration", {}))
    return converted


def build_things() -> None:
    source = load_json(V2DB / "org.eclipse.smarthome.core.thing.Thing.json")
    things = {}

    for key in sorted(source):
        value = source[key]["value"]
        thing_type = colon(value["thingTypeUID"]["segments"])
        converted = {
            "isBridge": thing_type == "mqtt:broker",
            "channels": (
                exec_channels(key)
                if thing_type == "exec:command"
                else [convert_channel(channel) for channel in value.get("channels", [])]
            ),
            "label": value.get("label", ""),
        }
        if thing_type == "mqtt:topic":
            # The v2 broker had retainMessages=true, so late subscribers such
            # as a restarted OneUI kiosk immediately received the last state.
            # v5 has no broker-wide retain option; set it on every outgoing
            # channel instead.
            for channel in converted["channels"]:
                if channel["configuration"].get("commandTopic"):
                    channel["configuration"]["retained"] = True
        if value.get("bridgeUID"):
            converted["bridgeUID"] = colon(value["bridgeUID"]["segments"])

        config = flat_config(value.get("configuration", {}))
        if thing_type == "exec:command":
            # These maintenance scripts call privileged commands internally.
            # Run the whole script through sudo so /etc/sudoers.d/hestia only
            # has to allow the script paths.
            command = config.get("command", "")
            for name in ("backup.sh", "restore.sh", "reboot.sh", "shutdown.sh"):
                plain = f"/home/pi/scripts/{name}"
                if command == plain or command == f"{plain} &":
                    config["command"] = f"sudo {command}"
                    break
            transform = config.get("transform", "REGEX((.*))")
            config["transform"] = transform if isinstance(transform, list) else [transform]
            converted["properties"] = {"thingTypeVersion": "1"}
        elif thing_type == "mqtt:broker":
            # v2 broker-only option, not a v5 broker parameter.
            config.pop("retainMessages", None)
            converted["properties"] = value.get("properties", {})
        else:
            converted["properties"] = value.get("properties", {})

        converted["configuration"] = config
        converted["UID"] = key
        converted["thingTypeUID"] = thing_type
        if value.get("location"):
            converted["location"] = value["location"]
        things[key] = {"class": THING_CLASS, "value": converted}

    # Replace the removed local gpio binding with one exec thing per pin.
    for pin in PIN_IDS:
        thing_uid = f"exec:command:setpin{pin}"
        things[thing_uid] = {
            "class": THING_CLASS,
            "value": {
                "isBridge": False,
                "channels": exec_channels(thing_uid),
                "label": f"Set GPIO Pin {pin}",
                "configuration": {
                    "command": f"/usr/bin/sudo /home/pi/scripts/hestia-setgpio.sh {pin} %2$s",
                    "autorun": True,
                    "interval": 0,
                    "timeout": 5,
                    "transform": ["REGEX((.*))"],
                },
                "properties": {"thingTypeVersion": "1"},
                "UID": thing_uid,
                "thingTypeUID": "exec:command",
                "location": "GPIO",
            },
        }

    write_json(DB / "org.openhab.core.thing.Thing.json", things)


def build_links() -> None:
    source = load_json(V2DB / "org.eclipse.smarthome.core.thing.link.ItemChannelLink.json")
    items = load_json(DB / "org.openhab.core.items.Item.json")
    things = load_json(DB / "org.openhab.core.thing.Thing.json")
    channels = {
        channel["uid"]
        for thing in things.values()
        for channel in thing["value"].get("channels", [])
    }
    links = {}
    skipped = []

    for key in sorted(source):
        value = source[key]["value"]
        segments = value["channelUID"]["segments"]
        channel_uid = colon(segments)
        # The v2 JSONDB contains one dead link to the removed TempSetpoint
        # item/channel.  Keeping it makes v5 reject the link on startup.
        if value["itemName"] not in items or channel_uid not in channels:
            skipped.append(key)
            continue
        links[key] = {
            "class": LINK_CLASS,
            "value": {
                "channelUID": {"segments": segments, "uid": channel_uid},
                "configuration": {"properties": flat_config(value.get("configuration", {}))},
                "itemName": value["itemName"],
            },
        }

    # v2 Switch item -> gpio binding.  v5 uses the tested Nashorn transform
    # profile to turn ON/OFF into the 1/0 expected by the sysfs helper.
    for pin in PIN_IDS:
        channel_uid = f"exec:command:setpin{pin}:input"
        segments = channel_uid.split(":")
        key = f"Pin{pin} -> {channel_uid}"
        links[key] = {
            "class": LINK_CLASS,
            "value": {
                "channelUID": {"segments": segments, "uid": channel_uid},
                "configuration": {
                    "properties": {
                        "profile": "transform:NASHORNJS",
                        "commandFromItemScript": "|(input == 'ON') ? '1' : '0'",
                    }
                },
                "itemName": f"Pin{pin}",
            },
        }

    write_json(DB / "org.openhab.core.thing.link.ItemChannelLink.json", links)
    if skipped:
        print("links: skipped dead v2 link(s): " + ", ".join(skipped))


def fix_rule_script(script: str, rule_name: str) -> str:
    # v5 GroupItem is not iterable and events.sendCommand needs the item name.
    if rule_name == "Main Switch turned off":
        script = script.replace(
            'for each (var rem in ir.getItem("RemBoostTimes")) {\n'
            '  events.sendCommand(rem, 0);\n'
            '}',
            'for each (var rem in ir.getItem("RemBoostTimes").members) {\n'
            '  events.sendCommand(rem.name, 0);\n'
            '}',
        )

    # Java Stream has count(), not the v2 collection-style size property.
    if rule_name == "SystemType changed":
        script = script.replace(
            ".filter(notOff).size > 0",
            ".filter(notOff).count() > 0",
        )

    # v5 does not keep the rule event in scope inside a timer callback.
    # Capture the event fields before creating the timer (same behaviour as v2).
    if rule_name == "Synchronize Temp Proxies":
        script = script.replace(
            '// If the change is from MyTempProxyF or MyTempProxyC, forward the new value\n'
            '// to MyTempProxy\n'
            'var toItem = (event.itemName.endsWith("F") || event.itemName.endsWith("C")) ? '
            'event.itemName.substring(0, event.itemName.length-1) : event.itemName+items["TempUnit"];\n'
            '\n'
            'createTimerSecs(1, function() {\n'
            '  if(items[toItem] != items[event.itemName]) {\n'
            '    logDebug("tempproxy", "Commanding " + toItem + " with " + items[event.itemName]);\n'
            '    events.sendCommand(toItem, items[event.itemName]);\n'
            '  }\n'
            '});',
            '// If the change is from MyTempProxyF or MyTempProxyC, forward the new value\n'
            '// to MyTempProxy\n'
            'var eventItem = event.itemName;\n'
            'var toItem = (eventItem.endsWith("F") || eventItem.endsWith("C")) ? '
            'eventItem.substring(0, eventItem.length-1) : eventItem+items["TempUnit"];\n'
            '\n'
            'createTimerSecs(1, function() {\n'
            '  if(items[toItem] != items[eventItem]) {\n'
            '    logDebug("tempproxy", "Commanding " + toItem + " with " + items[eventItem]);\n'
            '    events.sendCommand(toItem, items[eventItem]);\n'
            '  }\n'
            '});',
        )

    # The v2 gpio binding reported the logical pin items as OFF at startup.
    # The v5 Exec replacement leaves an unused pin (for example CoolingPin on
    # a heat pump that has never cooled) at NULL.  The original guard required
    # CoolingPin == OFF before turning heating off, so with NULL it blocked
    # HeatingCtrl OFF forever.  Only an actually-ON cooling request should
    # prevent turning the heat off.
    if rule_name == "Heating/Cooling Check":
        old = '     (mode == "Heating" && items["CoolingPin"] == "OFF")) {'
        new = '     (mode == "Heating" && items["CoolingPin"] != "ON")) {'
        if old in script:
            script = script.replace(old, new)

    # The boost countdown timer also uses the event and the state value after
    # the rule has finished; capture both for the callback.  Apply the changes
    # line by line so whitespace differences between branches do not matter.
    if rule_name == "Boost Looping Timer":
        replacements = [
            (
                "  var runme = function(){",
                "  var boostItem = event.itemName;\n"
                "  var remaining = newState.intValue();\n"
                "  var runme = function(){",
            ),
            (
                "    var timeLeft = newState.intValue() - 1;",
                "    var timeLeft = remaining - 1;",
            ),
            (
                '"Minutes = " + newState + "  Time Left = "',
                '"Minutes = " + remaining + "  Time Left = "',
            ),
            (
                "    events.sendCommand(event.itemName, timeLeft.toString());",
                "    events.sendCommand(boostItem, timeLeft.toString());",
            ),
        ]
        for old, new in replacements:
            if old not in script:
                raise SystemExit(
                    "Boost Looping Timer: expected source line not found: " + old.strip()
                )
            script = script.replace(old, new)

    return script


def build_rules() -> None:
    source = load_json(V2DB / "automation_rules.json")
    items = load_json(DB / "org.openhab.core.items.Item.json")
    rules = {}
    script_count = 0
    skipped_triggers = []

    for uid in sorted(source):
        raw = source[uid]["value"]
        value = json.loads(json.dumps(raw))  # deep copy

        name = value["name"]

        # The v2 JSONDB still has a trigger for the removed TempSetpoint
        # item.  v5 cannot resolve it, so drop only that dead trigger.
        kept_triggers = []
        for trigger in value.get("triggers", []):
            item_name = trigger.get("configuration", {}).get("itemName")
            if item_name and item_name not in items:
                skipped_triggers.append(f"{name}: {trigger.get('label', item_name)}")
                continue
            kept_triggers.append(trigger)
        value["triggers"] = kept_triggers

        for kind in ("triggers", "conditions", "actions"):
            for module in value.get(kind, []):
                config = module.get("configuration", {})
                if config.get("type") == "application/javascript":
                    config["type"] = SCRIPT_TYPE
                    script_count += 1
                if config.get("script"):
                    config["script"] = fix_rule_script(config["script"], name)

        # The v2 HumidityBoostTime trigger was an empty ItemCommandTrigger.
        # v5 requires itemName; use the equivalent state-change trigger.
        if name == "BoostTime changed":
            replaced = False
            for trigger in value.get("triggers", []):
                if trigger.get("type") == "core.ItemCommandTrigger" and not trigger.get("configuration"):
                    trigger["type"] = "core.ItemStateChangeTrigger"
                    trigger["configuration"] = {"itemName": "HumidityBoostTime"}
                    replaced = True
            if not replaced:
                raise SystemExit("BoostTime changed: empty command trigger not found")

        scripts = [
            module.get("configuration", {}).get("script", "")
            for kind in ("conditions", "actions")
            for module in value.get(kind, [])
        ]
        if name == "Main Switch turned off" and not any(
            'ir.getItem("RemBoostTimes").members' in script and "rem.name" in script
            for script in scripts
        ):
            raise SystemExit("Main Switch turned off: v5 group fix was not applied")
        if name == "SystemType changed" and not any(
            ".filter(notOff).count() > 0" in script for script in scripts
        ):
            raise SystemExit("SystemType changed: v5 stream fix was not applied")
        if name == "Synchronize Temp Proxies" and not any(
            "var eventItem = event.itemName;" in script
            and "items[eventItem]" in script
            and "items[event.itemName]" not in script
            for script in scripts
        ):
            raise SystemExit("Synchronize Temp Proxies: timer event capture was not applied")
        if name == "Boost Looping Timer" and not any(
            "var boostItem = event.itemName;" in script
            and "var remaining = newState.intValue();" in script
            and "events.sendCommand(event.itemName" not in script
            for script in scripts
        ):
            raise SystemExit("Boost Looping Timer: timer event capture was not applied")

        rules[uid] = {
            "class": RULE_CLASS,
            "value": {
                "triggers": value.get("triggers", []),
                "conditions": value.get("conditions", []),
                "actions": value.get("actions", []),
                "configuration": value.get("configuration", {}),
                "configDescriptions": value.get("configDescriptions", []),
                "templateState": "no-template",
                "uid": value["uid"],
                "name": name,
                "tags": value.get("tags", []),
                "visibility": value.get("visibility", "VISIBLE"),
                "description": value.get("description", ""),
            },
        }

    write_json(DB / "automation_rules.json", rules)
    write_json(DB / "automation_rules_disabled.json",
               load_json(V2DB / "automation_rules_disabled.json"))
    if skipped_triggers:
        print("rules: skipped dead v2 trigger(s): " + ", ".join(skipped_triggers))
    print(f"rules: {len(rules)} converted, {script_count} scripts switched to Nashorn")


# ---------------------------------------------------------------------------
# Text configuration
# ---------------------------------------------------------------------------

def build_conf() -> None:
    copy_file(V2CONF / "automation" / "lib" / "hestia" / "defaults.js",
              CONF / "automation" / "lib" / "hestia" / "defaults.js")
    copy_file(TEMPLATES / "utils.js",
              CONF / "automation" / "lib" / "hestia" / "utils.js")
    write_text(CONF / "automation" / "jsr223" / "hestia_startup.nashornjs",
               STARTUP_SCRIPT)
    write_text(CONF / "persistence" / "mapdb.persist", MAPDB_PERSIST)
    copy_file(V2CONF / "services" / "mapdb.cfg",
              CONF / "services" / "mapdb.cfg")
    copy_file(V2CONF / "sitemaps" / "default.sitemap",
              CONF / "sitemaps" / "default.sitemap")
    copy_file(V2CONF / "transform" / "binary.map",
              CONF / "transform" / "binary.map")

    for relative, text in CONF_PLACEHOLDERS.items():
        write_text(CONF / relative, text)

    write_text(CONF / "rules" / "default.rules", DEFAULT_RULES)
    write_text(CONF / "services" / "addons.cfg", ADDONS_CFG)

    # Build the exec whitelist from the generated things.  This guarantees the
    # exact string, including any %2$s placeholder, is whitelisted.
    things = load_json(DB / "org.openhab.core.thing.Thing.json")
    commands = sorted({
        value["value"]["configuration"]["command"]
        for value in things.values()
        if value["value"].get("thingTypeUID") == "exec:command"
        and value["value"].get("configuration", {}).get("command")
    })
    write_text(CONF / "misc" / "exec.whitelist", "\n".join(commands) + "\n")


def copy_file(source: Path, target: Path, mode: int | None = None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if mode is not None:
        target.chmod(mode)


# ---------------------------------------------------------------------------
# HestiaPi scripts
# ---------------------------------------------------------------------------

TEXT_REPLACEMENTS = (
    ("#!/usr/bin/python", "#!/usr/bin/python3"),
    ("/etc/openhab2", "/etc/openhab"),
    ("/var/lib/openhab2", "/var/lib/openhab"),
    ("service openhab2", "service openhab"),
    ("systemctl stop openhab2", "systemctl stop openhab"),
    ("python-pip", "python3-pip"),
    ("python-dev", "python3-dev"),
    ("python-smbus", "python3-smbus"),
    ("sudo python setup.py install", "sudo python3 setup.py install"),
)

# The HestiaPi ONE board wires the BME280 to the GPIO I2C bus, which the
# Bookworm kernel exposes as i2c-2 (older images used i2c-1).  Probe the
# common buses and both sensor addresses so one image works on either board.
BME280_DISCOVERY_BLOCK = """\
bus = None
device = 0x76 # Default device I2C address
for attempt in range(5):
    for bus_id in (1, 2, 0):
        try:
            candidate = smbus.SMBus(bus_id)
        except (IOError, OSError):
            continue
        for device_id in (0x76, 0x77):
            try:
                (chip_id, chip_version) = candidate.read_i2c_block_data(device_id, 0xD0, 2)
                bus = candidate
                device = device_id
                break
            except (IOError, OSError):
                pass
        if bus is not None:
            break
    if bus is not None:
        break
    time.sleep(0.5)

if bus is None:
    raise RuntimeError("No BME280 sensor found on i2c buses 1, 2 or 0")
"""


def convert_script_text(text: str, name: str) -> str:
    if name == "getssid.sh":
        old = """/sbin/iwconfig wlan0 | grep -oP 'ESSID:"\\K[^"]*'"""
        new = r"/sbin/iw dev wlan0 link | grep -oP 'SSID:\s*\K.*'"
        if old not in text:
            raise SystemExit("getssid.sh: expected iwconfig command not found")
        text = text.replace(old, new)

    if name == "getwifiinfo.sh":
        old = (
            "RSSI=$(/sbin/iwconfig wlan0 | grep 'Signal level' | "
            "awk '{print $4}' | sed 's/.*[=:]//g' | cut -d/ -f1);"
        )
        new = "RSSI=$(/sbin/iw dev wlan0 link | grep 'signal:' | awk '{print $2}');"
        if old not in text:
            raise SystemExit("getwifiinfo.sh: expected iwconfig command not found")
        text = text.replace(old, new)

    pin_script = re.fullmatch(r"pin(\d+)_(on|off)\.sh", name)
    if pin_script:
        pin = int(pin_script.group(1))
        value = 1 if pin_script.group(2) == "on" else 0
        old = f"/usr/bin/gpioset gpiochip0 {pin}={value}"
        if old not in text:
            raise SystemExit(f"{name}: expected gpioset command not found")
        new = (
            "# The board-specific GPIO mapping lives in hestia-setgpio.sh,\n"
            "# so this script works on both Raspberry Pi and Orange Pi.\n"
            f"/usr/bin/sudo /home/pi/scripts/hestia-setgpio.sh {pin} {value}"
        )
        text = text.replace(old, new)

    if name == "updateone.sh":
        return (
            "#!/bin/bash\n"
            "# The HestiaPi ONE updater pulls the openHAB 2 tree.  On openHAB 5\n"
            "# restore the v5 backup ZIP instead of running that updater.\n"
            'echo "Automatic update is disabled on openHAB 5. Restore the v5 backup ZIP."\n'
            "exit 0\n"
        )

    for old, new in TEXT_REPLACEMENTS:
        text = text.replace(old, new)

    # Replace the python interpreter in shell invocations, but leave the
    # already converted python3 and package names alone.
    text = re.sub(r"(?<![\w./-])python(?![\w.-])", "python3", text)
    return text


def build_scripts() -> None:
    if OUT_SCRIPTS.exists():
        shutil.rmtree(OUT_SCRIPTS)
    OUT_SCRIPTS.mkdir(parents=True, exist_ok=True)

    text_suffixes = {".sh", ".py"}
    for source in sorted(SCRIPTS.rglob("*")):
        if source.is_dir():
            continue
        relative = source.relative_to(SCRIPTS)
        target = OUT_SCRIPTS / relative
        target.parent.mkdir(parents=True, exist_ok=True)

        if source.suffix in text_suffixes:
            text = source.read_text(encoding="utf-8", errors="surrogateescape")
            text = convert_script_text(text, source.name)
            target.write_text(text, encoding="utf-8", errors="surrogateescape", newline="\n")
            target.chmod(0o755 if source.suffix == ".sh" or os.access(source, os.X_OK) else 0o644)
        else:
            shutil.copy2(source, target)

    helper = SETGPIO.replace(
        'DEFAULT_BOARD="auto"', f'DEFAULT_BOARD="{GPIO_BOARD_DEFAULT}"'
    )
    write_text(OUT_SCRIPTS / "hestia-setgpio.sh", helper, 0o755)
    write_text(SETUP / "hestia-sudoers", SUDOERS, 0o440)
    write_text(SETUP / "hestia-setgpio.sh", helper, 0o755)
    copy_file(TEMPLATES / "install.sh", SETUP / "install.sh", 0o755)
    copy_file(TEMPLATES / "kiosk.service", SETUP / "kiosk.service", 0o644)
    copy_file(TEMPLATES / "kiosk-xinitrc", SETUP / "kiosk-xinitrc", 0o755)
    copy_file(TEMPLATES / "99-framebuffer-lcd.rules", SETUP / "99-framebuffer-lcd.rules", 0o644)
    copy_file(TEMPLATES / "set-kiosk-framebuffer", SETUP / "set-kiosk-framebuffer", 0o755)
    copy_file(TEMPLATES / "mosquitto-oneui.conf",
              SETUP / "mosquitto-oneui.conf", 0o644)
    copy_file(TEMPLATES / "xorg-20-kiosk.conf",
              SETUP / "xorg-20-kiosk.conf", 0o644)


# ---------------------------------------------------------------------------
# Restore package
# ---------------------------------------------------------------------------

def add_zip_file(archive: zipfile.ZipFile, path: Path, arcname: str) -> None:
    info = zipfile.ZipInfo(arcname, date_time=(2026, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    mode = path.stat().st_mode
    info.external_attr = (mode & 0xFFFF) << 16
    archive.writestr(info, path.read_bytes())


def build_zip() -> None:
    zip_path = OUT / "hestia-openhab-v5.zip"
    if zip_path.exists():
        zip_path.unlink()

    backup_properties = (
        "version=5.2.4\n"
        "timestamp=26_11_17-00_00_00\n"
        "user=openhab\n"
        "group=openhab\n"
    )
    write_text(OUT / "backup.properties", backup_properties)

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        add_zip_file(archive, OUT / "backup.properties", "backup.properties")
        for base, prefix in ((CONF, "conf"), (DB, "userdata/jsondb")):
            for path in sorted(base.rglob("*")):
                if path.is_file():
                    add_zip_file(archive, path, f"{prefix}/{path.relative_to(base).as_posix()}")
        for path in sorted(OUT_SCRIPTS.rglob("*")):
            if path.is_file():
                add_zip_file(archive, path, f"home/pi/scripts/{path.relative_to(OUT_SCRIPTS).as_posix()}")
        for path in sorted(SETUP.rglob("*")):
            if path.is_file():
                add_zip_file(archive, path, f"setup/{path.relative_to(SETUP).as_posix()}")
        for extra in ("RESTORE.md", "ADDONS_AND_SOFTWARE.md", "PORTING_NOTES.md"):
            source = ROOT / extra
            if source.exists():
                add_zip_file(archive, source, extra)
    print(f"zip: {zip_path} ({zip_path.stat().st_size} bytes)")


def validate_output() -> None:
    items = load_json(DB / "org.openhab.core.items.Item.json")
    things = load_json(DB / "org.openhab.core.thing.Thing.json")
    links = load_json(DB / "org.openhab.core.thing.link.ItemChannelLink.json")
    rules = load_json(DB / "automation_rules.json")
    channels = {
        channel["uid"]
        for thing in things.values()
        for channel in thing["value"].get("channels", [])
    }

    problems = []
    for key, link in links.items():
        value = link["value"]
        if value["itemName"] not in items:
            problems.append(f"link {key}: missing item {value['itemName']}")
        if value["channelUID"]["uid"] not in channels:
            problems.append(f"link {key}: missing channel {value['channelUID']['uid']}")

    for uid, rule in rules.items():
        for trigger in rule["value"].get("triggers", []):
            item_name = trigger.get("configuration", {}).get("itemName")
            if item_name and item_name not in items:
                problems.append(f"rule {uid} {rule['value']['name']}: missing item {item_name}")
        for kind in ("conditions", "actions"):
            for module in rule["value"].get(kind, []):
                module_type = module.get("configuration", {}).get("type")
                if module_type == "application/javascript":
                    problems.append(f"rule {uid} {rule['value']['name']}: unversioned JavaScript")

    heatcheck = next(
        (rule["value"] for rule in rules.values()
         if rule["value"]["name"] == "Heating/Cooling Check"),
        None,
    )
    if heatcheck is None:
        problems.append("missing Heating/Cooling Check rule")
    else:
        heatcheck_scripts = [
            module.get("configuration", {}).get("script", "")
            for kind in ("conditions", "actions")
            for module in heatcheck.get(kind, [])
        ]
        if not any('items["CoolingPin"] != "ON"' in script for script in heatcheck_scripts):
            problems.append(
                "Heating/Cooling Check still blocks heating OFF when CoolingPin is NULL"
            )

    exec_commands = sorted({
        thing["value"]["configuration"]["command"]
        for thing in things.values()
        if thing["value"].get("thingTypeUID") == "exec:command"
        and thing["value"].get("configuration", {}).get("command")
    })
    whitelist = sorted(
        line for line in (CONF / "misc" / "exec.whitelist").read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    if exec_commands != whitelist:
        problems.append("exec whitelist does not exactly match exec thing commands")

    for thing in things.values():
        value = thing["value"]
        if value.get("thingTypeUID") != "mqtt:topic":
            continue
        for channel in value.get("channels", []):
            config = channel.get("configuration", {})
            if config.get("commandTopic") and not config.get("retained"):
                problems.append(
                    f"mqtt channel {channel['uid']} publishes without retain"
                )

    for name in ("bme280.py", "bme280F.py", "bme280C.py"):
        script = (OUT_SCRIPTS / name).read_text(encoding="utf-8", errors="ignore")
        if BME280_DISCOVERY_BLOCK not in script:
            problems.append(f"{name} does not probe the supported I2C buses")

    for name in ("getssid.sh", "getwifiinfo.sh"):
        script = (OUT_SCRIPTS / name).read_text(encoding="utf-8")
        if "iwconfig" in script or "/sbin/iw dev wlan0 link" not in script:
            problems.append(f"{name} still depends on the removed iwconfig tool")

    gpio_helper = OUT_SCRIPTS / "hestia-setgpio.sh"
    helper_text = gpio_helper.read_text(encoding="utf-8")
    if f'DEFAULT_BOARD="{GPIO_BOARD_DEFAULT}"' not in helper_text:
        problems.append("hestia-setgpio.sh does not use the requested default board")
    gpio_expected = {
        "rpi": {12: 12, 16: 16, 18: 18, 20: 20, 23: 23},
        "orangepi-zero2w": {12: 267, 16: 76, 18: 257, 20: 260, 23: 270},
        "generic": {12: 12, 16: 16, 18: 18, 20: 20, 23: 23},
    }
    for board, pins in gpio_expected.items():
        for pin, gpio in pins.items():
            result = subprocess.run(
                ["/bin/bash", str(gpio_helper), str(pin), "1"],
                env={
                    **os.environ,
                    "HESTIA_GPIO_BOARD": board,
                    "HESTIA_GPIO_DRY_RUN": "1",
                },
                capture_output=True,
                text=True,
                check=False,
            )
            expected = f"board={board} logical={pin} gpio={gpio} value=1"
            if result.returncode != 0 or expected not in result.stdout:
                problems.append(
                    f"hestia-setgpio.sh maps {board} pin {pin} incorrectly: "
                    f"{result.stdout.strip() or result.stderr.strip()}"
                )

    for pin in PIN_IDS:
        for state, value in (("on", 1), ("off", 0)):
            script = (OUT_SCRIPTS / f"pin{pin}_{state}.sh").read_text(encoding="utf-8")
            expected = f"/usr/bin/sudo /home/pi/scripts/hestia-setgpio.sh {pin} {value}"
            if expected not in script or "gpioset gpiochip0" in script:
                problems.append(
                    f"pin{pin}_{state}.sh does not use the board-aware GPIO helper"
                )

    addons = (CONF / "services" / "addons.cfg").read_text(encoding="utf-8")
    for required in (
        "automation = jsscriptingnashorn",
        "binding = mqtt,exec",
        "persistence = mapdb",
        "transformation = regex",
        "ui = basic",
    ):
        if required not in addons:
            problems.append(f"addons.cfg is missing '{required}'")

    persist = (CONF / "persistence" / "mapdb.persist").read_text(encoding="utf-8")
    if "default =" in persist or "default=" in persist:
        problems.append("mapdb.persist still uses the removed openHAB 5 default strategy")
    if "RestoreOnStartup* : strategy = everyUpdate, restoreOnStartup" not in persist:
        problems.append("mapdb.persist does not restore RestoreOnStartup members")

    for relative in CONF_PLACEHOLDERS:
        if not (CONF / relative).is_file():
            problems.append(f"missing conf placeholder {relative}")

    for relative in (
        "setup/install.sh",
        "setup/kiosk.service",
        "setup/kiosk-xinitrc",
        "setup/mosquitto-oneui.conf",
        "setup/xorg-20-kiosk.conf",
    ):
        if not (OUT / relative).is_file():
            problems.append(f"missing restore file {relative}")

    if not (OUT_SCRIPTS / "oneui" / "index.html").is_file():
        problems.append("missing home/pi/scripts/oneui/index.html")

    kiosk_service = (SETUP / "kiosk.service").read_text(encoding="utf-8")
    if "/usr/local/oneui/kiosk-xinitrc" not in kiosk_service:
        problems.append("kiosk.service does not start the installed OneUI session")

    kiosk_xinitrc = (SETUP / "kiosk-xinitrc").read_text(encoding="utf-8")
    if "file:///usr/local/oneui/index.html" not in kiosk_xinitrc:
        problems.append("kiosk-xinitrc does not load /usr/local/oneui/index.html")

    installer = (SETUP / "install.sh").read_text(encoding="utf-8")
    if "/usr/local/oneui" not in installer or "9001" not in installer:
        problems.append("install.sh does not install OneUI and the MQTT WebSocket listener")
    if "usermod -aG i2c openhab" not in installer:
        problems.append("install.sh does not give the openhab user i2c access")

    startup_script = CONF / "automation" / "jsr223" / "hestia_startup.nashornjs"
    if not startup_script.is_file():
        problems.append("missing automation/jsr223/hestia_startup.nashornjs")
    else:
        script = startup_script.read_text(encoding="utf-8")
        if "StartInitialization" not in script or "createTimerSecs" not in script:
            problems.append("hestia_startup.nashornjs does not kick off initialization")

    default_rules = (CONF / "rules" / "default.rules").read_text(encoding="utf-8")
    if "createTimer(now.plusSeconds(60)" not in default_rules:
        problems.append("default.rules does not defer its initialization fallback")

    if problems:
        for problem in problems:
            print("VALIDATION ERROR: " + problem)
        raise SystemExit(1)
    print("validation: ok")


def main() -> None:
    global GPIO_BOARD_DEFAULT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gpio-board",
        choices=("auto", "rpi", "orangepi-zero2w", "generic"),
        default="auto",
        help=(
            "default GPIO mapping baked into hestia-setgpio.sh; 'auto' "
            "detects the board at runtime and produces one ZIP for both "
            "Raspberry Pi and Orange Pi (default: auto)"
        ),
    )
    args = parser.parse_args()
    GPIO_BOARD_DEFAULT = args.gpio_board

    if OUT.exists():
        shutil.rmtree(OUT)
    CONF.mkdir(parents=True)
    DB.mkdir(parents=True)

    build_items()
    build_metadata()
    build_things()
    build_links()
    build_rules()
    build_conf()
    build_scripts()
    build_zip()
    validate_output()

    print(f"items: {len(load_json(DB / 'org.openhab.core.items.Item.json'))}")
    print(f"things: {len(load_json(DB / 'org.openhab.core.thing.Thing.json'))}")
    print(f"links: {len(load_json(DB / 'org.openhab.core.thing.link.ItemChannelLink.json'))}")
    print(f"output: {OUT}")


if __name__ == "__main__":
    main()
