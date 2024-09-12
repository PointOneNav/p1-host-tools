import logging
import os
import pathlib
import re
import sys
from enum import Enum
from typing import List, Optional, Tuple

import serial
from serial.tools.list_ports import comports

_logger = logging.getLogger('point_one.quectel.find_serial_device')


class PortType(Enum):
    # The CP210x driver names UART1 the "Enhanced" port.
    ENHANCED = 1
    UART1 = 1

    # The CP210x driver names UART2 the "Standard" port.
    STANDARD = 2
    UART2 = 2

    # Return either UART, favoring Standard if found and not open.
    ANY = 3

    def __str__(self):
        return super().__str__().replace(self.__class__.__name__ + '.', '')


# The serial port object returned by comports() looks as follows:
# - Linux:
#   - port.manufacturer: Silicon Labs
#   - port.product: CP2105 Dual USB to UART Bridge Controller
#   - port.description: CP2105 Dual USB to UART Bridge Controller - Standard Com Port
# - Windows:
#   - port.manufacturer: Silicon Labs
#   - port.product: None
#   - port.description: Silicon Labs Dual CP2105 USB to UART Bridge: Standard COM Port (COM42)
# - Mac OS:
#   - port.manufacturer: Silicon Labs
#   - port.product: CP2105 Dual USB to UART Bridge Controller
#   - port.description: CP2105 Dual USB to UART Bridge Controller
#   ^ Note: The Mac OS driver does not include "standard"/"enhanced" in the port description
#
# On some driver versions the manufacturer is "Silicon Laboratories" instead of "Silicon Labs"
def _is_cp210x(port):
    if port.manufacturer is None or port.description is None:
        return False
    return 'Silicon Lab' in port.manufacturer and 'CP210' in port.description


def _get_type(port):
    # There is no reliable way to determine which port is which on Mac OS. Empirically, the two ports seem to enumerate
    # as:
    #   /dev/cu.SLAB_USBtoUART     Standard
    #   /dev/cu.SLAB_USBtoUART3    Enhanced
    # but it's not clear if that's deterministic all the time. For now, we'll assume it is.
    if sys.platform == "darwin":
        if port.device == "/dev/cu.SLAB_USBtoUART":
            return PortType.STANDARD
        else:
            return PortType.ENHANCED
    else:
        m = re.match(r'.* (Standard|Enhanced) (?:Com|COM) Port', port.description)
        if m is None:
            raise ValueError("Unable to determine type of port %s." % port.device)
        else:
            if m.group(1) == "Standard":
                return PortType.STANDARD
            else:
                return PortType.ENHANCED


def find_serial_device(port_name: str = 'auto', port_type: Optional[PortType] = PortType.STANDARD,
                       on_wrong_type: str = 'warn') -> str:
    """!
    @brief Find the serial port belonging to a Quectel LG69T device.

    The Quectel LG69T EVB uses the Silicon Labs CP210x USB-to-serial device to communicate with the chip. By default,
    this function will automatically search for the serial device belonging to the specified port. The CP210x ports are
    defined as follows for the LG69T:
    - Enhanced - Connected to UART1 on the LG69T (configured for NMEA data only by default)
    - Standard - Connected to UART2 on the LG69T (configured for FusionEngine, RTCM, and NMEA data by default)

    If `port_name` is a specific serial device on the host computer (e.g., `/dev/ttyUSB0`, `COM1`), that device will be
    used. If `on_wrong_type` is set to `raise` or `warn`, this function will verify that the named port is a CP210x, and
    that it is either the Standard or Enhanced port as requested by `port_type`.

    @param port_name If `auto`, automatically detect the requested port `type`. Otherwise, verify that the named device
           is a Silicon Labs CP210x with the requested port `type`.
    @param port_type The desired port on the Quectel device.
    @param on_wrong_type If `port_name` is a specific device (`port_name != 'auto'`), verify that the specified device
           matches `port_type`:
           - `raise` - Raise a `ValueError` if the requested port is not `port_type`
           - `warm` - Print a warning if the requested port is not `port_type`
           - `none` - Ignore `port_type`

    @return The name of the located serial device on the computer (`/dev/ttyUSB1`, 'COM3', etc.).
    """
    ports = comports()

    if port_name == 'auto':
        if port_type is None:
            raise ValueError('Port type must specified for automatic detection.')

        selected_port = None
        for port in ports:
            if _is_cp210x(port):
                try:
                    actual_type = _get_type(port)
                except ValueError as e:
                    # If we can't determine the port type, treat all ports as Enhanced (NMEA-only by default) if the
                    # user doesn't care which port they get.
                    if port_type == PortType.ANY:
                        actual_type = PortType.ENHANCED
                    # Otherwise, raise an exception.
                    else:
                        raise e

                if actual_type == PortType.STANDARD:
                    if port_type == PortType.STANDARD:
                        selected_port = port.device
                        break
                    elif port_type == PortType.ANY:
                        # Note: This Serial() call, and the one for the enhanced port below, are a poor man's attempt to
                        # check if the serial port is currently in use by another application. In practice, this has
                        # limited utility on some OSes, but it can't hurt.
                        #
                        # In Linux, this attempts to open the port with `fcntl.LOCK_EX | fcntl.LOCK_NB`. That will fail
                        # if and only if the other application has _also_ opened the port with those flags. Otherwise,
                        # it will succeed.
                        try:
                            device_serial = serial.Serial(port=port.device, exclusive=True)
                            selected_port = port.device
                            break
                        except serial.SerialException:
                            pass
                else:
                    if port_type == PortType.ENHANCED:
                        selected_port = port.device
                        break
                    elif port_type == PortType.ANY and selected_port is None:
                        try:
                            device_serial = serial.Serial(port=port.device, exclusive=True)
                            selected_port = port.device
                        except serial.SerialException:
                            pass

        if selected_port is None:
            raise ValueError("Serial device not found for %s port." % str(port_type).title())
        else:
            return selected_port
    else:
        for port in ports:
            if port.device == port_name:
                if not _is_cp210x(port):
                    message = "%s does not appear to be a Silicon Labs CP210x. Are you sure it's the right port?" % \
                              port.device
                    if on_wrong_type == 'raise':
                        raise ValueError(message)
                    elif on_wrong_type == 'warn':
                        _logger.warning("Warning: %s" % message)

                elif port_type is not None and port_type != PortType.ANY:
                    actual_type = None
                    try:
                        actual_type = _get_type(port)
                        if port_type == actual_type:
                            pass
                        else:
                            message = "Serial device %s matches CP210x type %s, not requested type %s." % \
                                (port.device, str(actual_type).title(), str(port_type).title())
                            if on_wrong_type == 'raise':
                                raise ValueError(message)
                            elif on_wrong_type == 'warn':
                                _logger.warning("Warning: %s" % message)
                    # This exception can occur for Quad CP210x.
                    except:
                        message = "Serial device %s matches CP210x type, but not standard/enhanced port labelling." % \
                                  port.device
                        if on_wrong_type == 'raise':
                            raise ValueError(message)
                        elif on_wrong_type == 'warn':
                            _logger.warning(message)

                return port.device

        # Even if a serial port isn't found, it could still be a symlink to an existing device. For example, a link
        # created by a udev rule.
        if os.path.exists(port_name) and os.path.islink(port_name):
            target_path = str(pathlib.Path(port_name).resolve())
            _logger.debug('Expanding symlink %s to %s.' % (port_name, target_path))
            find_serial_device(port_name=target_path, port_type=port_type, on_wrong_type=on_wrong_type)
            # We return the originally requested symlink, rather than the resolved path, to avoid any caller confusion.
            # The symlink should work correctly.
            return port_name
        # It could also be a virtual serial port.
        else:
            message = "Serial device %s not found." % port_name
            if on_wrong_type == 'raise':
                raise ValueError(message)
            elif on_wrong_type == 'warn':
                _logger.warning(message)
                return port_name


def find_serial_devices() -> List[Tuple[str, PortType]]:
    """!
    @brief Find all serial ports belonging to a Quectel LG69T device.

    See @ref find_serial_device() for details.

    @return A list containing one entry per serial port. Each entry is a tuple of the device name (`/dev/ttyUSB1`,
           'COM3', etc.) and the @ref PortType if available (Standard or Enhanced).
    """
    ports = comports()
    return [(port.device, _get_type(port)) for port in ports if _is_cp210x(port)]


if __name__ == "__main__":
    print('Any (auto): %s' % find_serial_device(port_type=PortType.ANY))
    print('Standard (auto): %s' % find_serial_device(port_type=PortType.STANDARD))
    print('Enhanced (auto): %s' % find_serial_device(port_type=PortType.ENHANCED))
    print('ttyUSB1 (Standard): %s' % find_serial_device('/dev/ttyUSB1', port_type=PortType.STANDARD))
    print('ttyUSB1 (None): %s' % find_serial_device('/dev/ttyUSB1', port_type=None))
    print('ttyUSB1 (Enhanced - Warning): %s' % find_serial_device('/dev/ttyUSB1', port_type=PortType.ENHANCED))
    try:
        print('ttyUSB1 (Enhanced - Mismatch): %s' % find_serial_device('/dev/ttyUSB1', port_type=PortType.ENHANCED,
                                                                       raise_on_wrong_type=True))
    except ValueError as e:
        print('ttyUSB1 (Enhanced - Exception): %s' % e)

    print('All:\n%s' % '\n'.join([repr(e) for e in find_serial_devices()]))
