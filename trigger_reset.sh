#!/usr/bin/env bash

# /etc/udev/rules.d/71-hid-relay.rules
# SUBSYSTEMS=="usb", ATTRS{idVendor}=="16c0", ATTRS{idProduct}=="05df", MODE:="0666"

/home/jdiamond/src/usb-relay-hid/commandline/makemake/hidusb-relay-cmd on 1
/home/jdiamond/src/usb-relay-hid/commandline/makemake/hidusb-relay-cmd off 1

#./firmware_tools/lg69t/firmware_tool.py quectel-lg69t-am.0.19.0-rc1-1006-g842ecae958-dirty.p1fw -f --reboot-cmd='./trigger_reset.sh' -m
