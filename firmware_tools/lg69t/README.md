# LG69T Firmware Tools

These tools are used to manage the firmware on the Quectel LG69T family of products.

The firmware consists of three files:
- Application software
- GNSS receiver firmware
- Bootloader (optional)

The tools require Python 3. They work in Linux, Windows and Mac.

Before using the tools, install the Python requirements:

```
python3 -m pip install -r requirements.txt
```

And download the latest firmware package from
[Point One's Developer Portal](https://pointonenav.com/docs/#standard_dev_kit).

## Updating Application and GNSS Firmware

> Note: To update the Application and GNSS firmware, you must use UART1 on the device (`Standard COM Port` for Windows,
typically `/dev/ttyUSB1` in Linux for P1SDK).

To update the firmware on a device, use the following steps:
1. Determine the correct serial port name to communicate with UART1 on your device.
   - In Windows, look for the COM port number of the "Standard COM Port" in Device Manager
   - In Linux/Mac OS, run `ls -l /dev/ttyUSB*`
2. Update the device, specifying the correct port and desired software version. The software will be downloaded
   automatically from the Point One Navigation server (requires an internet connection).

   For example, to update a device on `/dev/ttyUSBn` using LG69T-AM software version `A.B.C`, run the following:
   ```
   python3 firmware_tool.py --port=/dev/ttyUSBn --release lg69t-am-vA.B.C
   ```


### Updating Using A Downloaded `.p1fw` File

To update the firmware on a device from a locally downloaded file, use the following steps:
1. Download the latest `.p1fw` firmware file for your device from
   [Point One's Developer Portal](https://pointonenav.com/docs/#standard_dev_kit).
2. Determine the correct serial port name to communicate with UART1 on your device.
   - In Windows, look for the COM port number of the "Standard COM Port" in Device Manager
   - In Linux/Mac OS, run `ls -l /dev/ttyUSB*`
3. Update the device, specifying the correct port and desired path to the downloaded `.p1fw` file.

   For example, to update a device on `/dev/ttyUSBn` using LG69T-AM firmware version `A.B.C`, run the following:
   ```
   python3 firmware_tool.py --port=/dev/ttyUSBn /path/to/quectel-lg69t-am-A.B.C.p1fw
   ```

### Updating Only One Component

If desired, you can use the `--type` argument to update just one component. For example, to update only the application
software:
```
python3 firmware_tool.py --port=/dev/ttyUSBn --type=app lg69t-am-vA.B.C
```

### Updating Using `.bin` Files (Not Common)

You can also update the firmware using `.bin` files if needed. This is not common for most applications. 

```
python3 firmware_tool.py --port=/dev/ttyUSBn --typ=app /path/to/quectel-lg69t-am-A.B.C_upg.bin
```

## Considerations

By default, the update process will automatically reboot the device. If this is not working correctly, you may need to
specify the `--manual-reboot` argument, and then power cycle the device manually when prompted.

## Updating The Bootloader

> Note: In general, you should never need to reprogram the bootloader. Doing so will completely erase the chip,
> including any saved configuration, calibration, and the application firmware.

To program the bootloader:
1. Run `pip install stm32loader` to install the required programming tool.
2. Turn the device off, or press and hold the `RESET` button. 
3. Press and hold the `BOOT` button.
4. Power on the module or release the `RESET` button.
5. Release the `BOOT` button after the device is powered up.
6. Run `stm32loader -p /dev/ttyUSBn -e -w -v -a 0x08000000 quectel-bootloader-X.Y.Z.bin`
   - Specify the correct UART1 serial port for your machine.
7. Press the `RESET` button to complete the process.
8. Reload the application software as described in
   [Updating Application and GNSS Firmware](#updating-application-and-gnss-firmware).
   - After a bootloader change, you must specify `--manual-reboot` and restart the device manually when prompted.
