#!/bin/bash
# Returns Wifi signal strength in percentage
RSSI=$(/sbin/iw dev wlan0 link | grep 'signal:' | awk '{print $2}');
#RSSI=-99
#echo $RSSI

if [ -z "$RSSI" ]; then
  echo "-"
elif [ $RSSI -le -100 -o $RSSI -eq 0 ]; then
  echo "0"
elif [ $RSSI -ge -50 ]; then
  echo "100"
else
  echo $[2 * (RSSI + 100)] | bc
fi
