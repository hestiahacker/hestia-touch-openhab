#!/bin/bash

/sbin/iw dev wlan0 link | grep -oP 'SSID:\s*\K.*'
