#!/bin/bash

python3 /home/pi/scripts/bme280.py | grep Temperature | awk '{print $3}' | cut -c1-4
