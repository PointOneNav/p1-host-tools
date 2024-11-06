#!/usr/bin/env python3

import argparse
import json
import os
import struct
import sys
import time
import typing
import urllib.error
import urllib.request
import zlib
from enum import Enum, auto
from zipfile import ZipFile

from serial import Serial
from fusion_engine_client.parsers import FusionEngineEncoder, FusionEngineDecoder
from fusion_engine_client.messages import *

SYNC_WORD1 = 0x514C1309
SYNC_WORD1_BYTES = struct.pack('<I', SYNC_WORD1)
RSP_WORD1 = 0xAAFC3A4D
RSP_WORD1_BYTES = struct.pack('<I', RSP_WORD1)
SYNC_WORD2 = 0x1203A504
SYNC_WORD2_BYTES = struct.pack('<I', SYNC_WORD2)
RSP_WORD2 = 0x55FD5BA0
RSP_WORD2_BYTES = struct.pack('<I', RSP_WORD2)

CLASS_GNSS = b'\x01'
CLASS_APP = b'\x02'

MSG_ID_FIRMWARE_ADDRESS = b'\x01'
MSG_ID_FIRMWARE_INFO = b'\x02'
MSG_ID_START_UPGRADE = b'\x03'
MSG_ID_SEND_FIRMWARE = b'\x04'

# The manual indicates this should be 0, but here I account for the bootloader.
APP_FLASH_OFFSET = 0x20000

PACKET_SIZE = 1024 * 5

RESPONSE_PAYLOAD_SIZE = 4
HEADER = b'\xAA'
TAIL = b'\x55'


def _send_fe_and_wait(ser: Serial, request: MessagePayload, expected_response_type: MessageType,
                      timeout: float = 1.0, repeat_interval: float = 0.5) -> MessagePayload:
    encoder = FusionEngineEncoder()
    data = encoder.encode_message(request)

    decoder = FusionEngineDecoder()
    start_time = time.time()
    last_send_time = 0
    while time.time() < start_time + timeout:
        # Send the request once immediately, then again every N seconds if we haven't gotten a response.
        if time.time() > last_send_time + repeat_interval:
            ser.write(data)
            last_send_time = time.time()

        # Read all incoming data and wait for the expected response type.
        messages = decoder.on_data(ser.read_all())
        for header, payload in messages:
            if header.message_type == expected_response_type:
                return payload

    # Read timed out.
    return None


def query_version_info(ser: Serial, timeout: float = 2.0) -> VersionInfoMessage:
    return _send_fe_and_wait(ser, request=MessageRequest(MessageType.VERSION_INFO),
                             expected_response_type=MessageType.VERSION_INFO,
                             timeout=timeout, repeat_interval=0.5)


def send_reboot(ser: Serial, timeout: float = 10.0, reboot_flag: int = ResetRequest.REBOOT_NAVIGATION_PROCESSOR) \
        -> bool:
    response = _send_fe_and_wait(ser, request=ResetRequest(reboot_flag),
                                 expected_response_type=MessageType.COMMAND_RESPONSE,
                                 timeout=timeout, repeat_interval=0.5)
    if response is None:
        return False
    else:
        if response.response == Response.OK:
            return True
        else:
            print(f'Reboot command rejected: {response.response}')
            return False


def synchronize(ser: Serial, timeout=10.0):
    start_time = time.time()
    ser.timeout = 0.05
    resp_data = b'\x00\x00\x00\x00'
    while time.time() < start_time + timeout:
        ser.write(SYNC_WORD1_BYTES)
        c = ser.read()
        while len(c) > 0:
            resp_data = resp_data[1:] + c
            if resp_data == RSP_WORD1_BYTES:
                ser.write(SYNC_WORD2_BYTES)
                resp_data = ser.read(4)
                if len(resp_data) == 4 and resp_data == RSP_WORD2_BYTES:
                    return True
            c = ser.read()
    return False


def get_response(class_id: bytes, msg_id: bytes, ser: Serial, timeout=60):
    response_fmt = '>BBBHBBHIB'
    response_size = struct.calcsize(response_fmt)

    ser.timeout = timeout
    data = ser.read(response_size)
    if len(data) < response_size:
        print('Timeout waiting for response')
        return False

    _, _, _, read_payload_size, read_class_id, read_msg_id, response, crc, _ = struct.unpack(
        response_fmt, data)

    calculated_crc = zlib.crc32(data[1:-5])

    if RESPONSE_PAYLOAD_SIZE != read_payload_size:
        print(
            f"Response had unexpected size field. [expected={RESPONSE_PAYLOAD_SIZE}, got={read_payload_size}]")
        return False

    if class_id[0] != read_class_id:
        print(
            f"Response had class id field. [expected={class_id[0]}, got={read_class_id}]")
        return False

    if msg_id[0] != read_msg_id:
        print(
            f"Response had unexpected message id field. [expected={msg_id[0]}, got={read_msg_id}]")
        return False

    if crc != calculated_crc:
        print(
            f"Response had bad CRC. [calculated={calculated_crc}, got={crc}]")
        return False

    if response != 0:
        print(f"Response indicates error occurred. [error={response}]")
        return False

    return True


def encode_message(class_id: bytes, msg_id: bytes, payload: bytes):
    data = class_id + msg_id + struct.pack('>H', len(payload)) + payload
    crc = struct.pack('>I', zlib.crc32(data))
    return HEADER + data + crc + TAIL


def encode_app_info(firmware_data):
    app_info_fmt = '>IIIB3x'
    fw_crc = zlib.crc32(struct.pack('<I', len(firmware_data)) + firmware_data)
    payload_data = struct.pack(app_info_fmt, len(
        firmware_data), fw_crc, APP_FLASH_OFFSET, 0x01)
    return encode_message(CLASS_APP, MSG_ID_FIRMWARE_INFO, payload_data)


def encode_gnss_info(firmware_data):
    gnss_info_fmt = '>IIIIIIBBB5x'
    fw_crc = zlib.crc32(struct.pack('<I', len(firmware_data)) + firmware_data)
    payload_data = struct.pack(gnss_info_fmt, len(
        firmware_data), fw_crc, 0x10000000, 0x00000400, 0x00180000, 0x00080000, 0x01, 0x00, 0x00)
    return encode_message(CLASS_GNSS, MSG_ID_FIRMWARE_INFO, payload_data)


def send_firmware(ser: Serial, class_id: bytes, firmware_data):
    sequence_num = 0
    total_len = len(firmware_data)
    while len(firmware_data) > 0:
        data = encode_message(class_id, MSG_ID_SEND_FIRMWARE, struct.pack(
            '>I', sequence_num) + firmware_data[:PACKET_SIZE])
        ser.write(data)
        if not get_response(class_id, MSG_ID_SEND_FIRMWARE, ser):
            print()
            return False
        firmware_data = firmware_data[PACKET_SIZE:]
        sequence_num += 1
        print(
            f'\r{int((total_len - len(firmware_data))/total_len * 100.):02d}%', end='')
    print()
    return True


class UpgradeType(Enum):
    APP = auto()
    GNSS = auto()


def Upgrade(ser: Serial, bin_file: typing.BinaryIO, upgrade_type: UpgradeType, should_send_reboot: bool,
            wait_for_reboot: bool = False):
    class_id = {
        UpgradeType.APP: CLASS_APP,
        UpgradeType.GNSS: CLASS_GNSS,
    }[upgrade_type]

    if should_send_reboot:
        print('Rebooting the device...')

        # Send a FusionEngine reboot request with a reasonably short timeout. If the software is running, this
        # should take effect right away. If the device is not running (halted, software corrupted, etc.), this will
        # timeout and fall through to synchronization, which waits for the bootloader. When that happens, either:
        # 1. That will eventually timeout too and the process will fail
        # 2. If the device is running but the software is stuck, the device should trigger an internal watchdog and
        #    reset on its own before sync times out (typically 3 seconds)
        if not send_reboot(ser, timeout=2.0):
            print('Timed out waiting for reboot command response. Waiting for automatic or manual reboot.')
        else:
            print('Reboot command accepted. Waiting for reboot.')
    else:
        print('Please reboot the device...')

    # Note that the reboot command can take over 5 seconds to kick in.
    if not synchronize(ser, timeout=10.0):
        print('Reboot sync timed out. Please reboot the device and try again.')
        return False
    else:
        print('Sync successful.')

    print('Sending firmware address.')
    ser.write(encode_message(
        class_id, MSG_ID_FIRMWARE_ADDRESS, b'\x00' * 4))
    if not get_response(class_id, MSG_ID_FIRMWARE_ADDRESS, ser):
        return False

    firmware_data = bin_file.read()

    print('Sending firmware info.')
    if upgrade_type == UpgradeType.GNSS:
        ser.write(encode_gnss_info(firmware_data))
    else:
        ser.write(encode_app_info(firmware_data))
    if not get_response(class_id, MSG_ID_FIRMWARE_INFO, ser):
        return False

    print('Sending upgrade start and flash erase (takes 30 seconds)...')
    ser.write(encode_message(
        class_id, MSG_ID_START_UPGRADE, b''))
    if not get_response(class_id, MSG_ID_START_UPGRADE, ser):
        return False

    print('Sending data...')
    if send_firmware(ser, class_id, firmware_data) is True:
        print('Update successful.')
        if should_send_reboot:
            # Send a no-op reset request message and wait for a response. This won't actually restart the device,
            # it just waits for it to start on its own after the update completes.
            #
            # Before we send the request, we first give the software a couple seconds to start up and be ready to
            # handle the request.
            print('Waiting for software to start...')
            time.sleep(2.0)
            if send_reboot(ser, reboot_flag=0, timeout=3.0):
                print('Device rebooted.')
            else:
                print('Timed out waiting for device. Please reboot the device manually.')
                if wait_for_reboot:
                    input('Press any key to continue...')
        else:
            print('Please reboot the device...')
            if wait_for_reboot:
                input('Press any key to continue...')
        return True


def print_bytes(byte_data):
    print(", ".join(
        [f'0x{c:02X}' for c in byte_data]
    ))


def extract_fw_files(p1fw):
    app_bin_fd = None
    gnss_bin_fd = None
    if isinstance(p1fw, ZipFile):
        # Extract filenames from info.json file.
        if 'info.json' in p1fw.namelist():
            info_json = json.load(p1fw.open('info.json', 'r'))

            app_filename = info_json['fusion_engine']['filename']
            gnss_filename = info_json['gnss_receiver']['filename']

            if app_filename in p1fw.namelist():
                app_bin_fd = p1fw.open(app_filename, 'r')

            if gnss_filename in p1fw.namelist():
                gnss_bin_fd = p1fw.open(gnss_filename, 'r')
        else:
            print('No info.json file found. Aborting.')
            sys.exit(1)
    else:
        if os.path.exists(os.path.join(p1fw, 'info.json')):
            # Extract filenames from info.json file.
            info_json_path = os.path.join(p1fw, 'info.json')
            info_json = json.load(open(info_json_path))

            app_filename = info_json['fusion_engine']['filename']
            gnss_filename = info_json['gnss_receiver']['filename']
            app_path = os.path.join(p1fw, app_filename)
            gnss_path = os.path.join(p1fw, gnss_filename)

            if os.path.exists(app_path):
                app_bin_fd = open(os.path.join(p1fw, app_filename), 'rb')

            if os.path.exists(gnss_path):
                gnss_bin_fd = open(os.path.join(p1fw, gnss_filename), 'rb')
        else:
            print('No info.json file found. Aborting.')
            sys.exit(1)

    if app_bin_fd is None and gnss_bin_fd is None:
        print('GNSS and application firmware files not found in given p1fw path. Aborting.')
        sys.exit(1)
    elif app_bin_fd is None:
        print('Application firmware file not found in given p1fw path. Aborting.')
        sys.exit(1)
    elif gnss_bin_fd is None:
        print('GNSS firmware file not found in given p1fw path. Aborting.')
        sys.exit(1)

    print('GNSS and application firmware files found in given p1fw path. Will use these files to upgrade.')
    return app_bin_fd, gnss_bin_fd, info_json


def download_release_file(version: str, output_dir: str = None):
    filename = f'quectel-{version.replace("-v", ".")}.p1fw'

    if output_dir is None:
        output_path = filename
    else:
        output_path = os.path.join(output_dir, filename)

    if os.path.exists(output_path):
        print(f'Using existing file: {output_path}')
    else:
        print(f'Downloading {filename}...')

        parent_dir = os.path.dirname(output_path)
        if parent_dir != "":
            os.makedirs(parent_dir, exist_ok=True)

        url = f'https://s3.amazonaws.com/files.pointonenav.com/quectel/lg69t/{filename}'
        try:
            urllib.request.urlretrieve(url, output_path)
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                raise ValueError(f"Encountered error downloading {url}. Please check the specified version string.") \
                    from e
            else:
                raise e

    return output_path


def main():
    execute_command = os.path.basename(sys.executable)
    if execute_command.startswith('python'):
        execute_command += ' ' + os.path.basename(__file__)

    parser = argparse.ArgumentParser(
        description="Update the firmware on a Point One LG69T device.",
        epilog="""\
EXAMPLE USAGE

Download the .p1fw file for the LG69T-AM version A.B.C release and use that to
update the software on the device (recommended; requires an internet
connection):
    %(command)s --release lg69t-am-vA.B.C

Specify the serial port of the device on your computer:
    %(command)s --port /dev/ttyUSB6 --release lg69t-am-vA.B.C

Display the current software/firmware versions on your device:
    %(command)s --show

Update the software on the device from a downloaded Point One .p1fw firmware
file (no internet connection required):
    %(command)s quectel-lg69t-am.A.B.C.p1fw

Update only the application software (not common):
    %(command)s --type app --release lg69t-am-vA.B.C
""" % {'command': execute_command})

    parser.add_argument('file', type=str, metavar="FILE", nargs='?',
                        help="The path to the .p1fw or .bin firmware file to be loaded.")

    parser.add_argument('-f', '--force', action='store_true',
                        help="Update the firmware, even if the current version matches the desired version.")
    parser.add_argument('-m', '--manual-reboot', action='store_true',
                        help="Don't try to send a software reboot. User must manually reset the device.")
    parser.add_argument('-r', '--release', action='store_true',
                        help="If set, treat FILE as a FusionEngine release version string (e.g., lg69t-am-vA.B.C) and "
                             "download the corresponding .p1fw file (requires an internet connection). If the file "
                             "already exists in the working directory, the download will be skipped.")
    parser.add_argument('-o', '--output-dir', type=str,
                        help="The output directory to save the release if the --release flag is set and the "
                             "corresponding release is downloaded. If the --release flag is not set, this flag will "
                             "be ignored.")
    parser.add_argument('-s', '--show', action='store_true',
                        help="Display the current software versions on the device and exit.")
    parser.add_argument('-t', '--type', type=str, metavar="TYPE", action='append', choices=('gnss', 'app'),
                        help="The type of update to perform: gnss, app. When using a .p1fw file, this option may be "
                             "specified multiple times to perform multiple updates at once. For example: "
                             "--mode=gnss --mode=app. By default, all updates will be performed.\n"
                             "\n"
                             "When using a .bin file, this argument is required to specify the type of FILE.")

    device = parser.add_argument_group('Device Options')
    device.add_argument('--port', type=str, default='/dev/ttyUSB1', help="The serial port of the device.")

    advanced = parser.add_argument_group('Advanced Options')
    advanced.add_argument('--gnss', type=str, metavar="FILE", default=None,
                          help="The path to the GNSS (Teseo) firmware .bin file to be loaded.")
    advanced.add_argument('--app', type=str, metavar="FILE", default=None,
                          help="The path to the application firmware .bin file to be loaded.")

    args = parser.parse_args()

    port_name = args.port
    should_send_reboot = not args.manual_reboot

    # Show software versions and exit.
    if args.show:
        with Serial(port_name, baudrate=460800) as ser:
            version_info = query_version_info(ser, timeout=2.0)
            if version_info is None:
                print('Version query timed out.')
                sys.exit(1)
            else:
                print(f'FusionEngine: {version_info.engine_version_str}')
                print(f'OS: {version_info.os_version_str}')
                print(f'GNSS Receiver: {version_info.rx_version_str}')
        sys.exit(0)

    # Parse input file options.
    p1fw_path = None
    gnss_bin_path = None
    app_bin_path = None
    if args.file is None:
        if args.release:
            print('You must specify a release version.')
            sys.exit(1)
        else:
            if args.gnss is not None:
                gnss_bin_path = args.gnss

            if args.app is not None:
                app_bin_path = args.app

            if gnss_bin_path is None and app_bin_path is None:
                print('You must specify an input filename.')
                sys.exit(1)
    else:
        if args.gnss is not None or args.app is not None:
            print('You cannot specify both FILE and --gnss/--app.')
            sys.exit(1)

        if args.release:
            if args.output_dir:
                args.file = download_release_file(args.file, args.output_dir)
            else:
                args.file = download_release_file(args.file)

        ext = os.path.splitext(args.file)[1]
        if ext == '.p1fw':
            p1fw_path = args.file
            if args.type is None:
                args.type = ('gnss', 'app')
        elif ext == '.bin':
            if args.type is None:
                print('You must specify --type when using a .bin file.')
                sys.exit(1)
            elif len(args.type) != 1:
                print('You may only specify a single --type when using a .bin file.')
                sys.exit(1)
            else:
                if args.type[0] == 'gnss':
                    gnss_bin_path = args.file
                elif args.type[0] == 'app':
                    app_bin_path = args.file
                else:
                    print('Unrecognized file type.')
                    sys.exit(1)
        else:
            print('Unrecognized file type.')
            sys.exit(1)

    # Open the input files.
    p1fw = None
    app_bin_fd = None
    gnss_bin_fd = None
    if p1fw_path is not None:
        if os.path.exists(p1fw_path):
            # Check if a directory is what was provided. If not, then it is assumed that a compressed
            # file is what was provided (this is the expected use case).
            if os.path.isdir(p1fw_path):
                p1fw = p1fw_path
            else:
                try:
                    p1fw = ZipFile(p1fw_path, 'r')
                except:
                    print('Provided path does not lead to a zip file or a directory.')
                    sys.exit(1)
        else:
            print('Provided path %s not found.' % p1fw_path)
            sys.exit(1)

    if p1fw is not None:
        app_bin_fd, gnss_bin_fd, info_json = extract_fw_files(p1fw)

        if 'app' not in args.type:
            app_bin_fd = None
        if 'gnss' not in args.type:
            gnss_bin_fd = None
    else:
        info_json = {}

    if gnss_bin_fd is not None:
        if gnss_bin_path is not None:
            print('Ignoring provided GNSS bin path, as p1fw path was provided.')
    elif gnss_bin_path is not None:
        gnss_bin_fd = open(gnss_bin_path, 'rb')

    if app_bin_fd is not None:
        if app_bin_path is not None:
            print('Ignoring provided application bin path, as p1fw path was provided.')
    elif app_bin_path is not None:
        app_bin_fd = open(app_bin_path, 'rb')

    if gnss_bin_fd is None and app_bin_fd is None:
        print('Error: Nothing to do.')
        sys.exit(1)

    # Perform the software update.
    print(f"Starting upgrade on device {port_name}.")
    with Serial(port_name, baudrate=460800) as ser:
        # If we have version information from a .p1fw file, query the software versions on the device and skip
        # unnecessary updates. If the device is not running, this query will fail and we'll go ahead and update
        # everything.
        version_info = None
        if info_json is not None:
            print('Checking current software version.')
            version_info = query_version_info(ser, timeout=2.0)
            if version_info is not None:
                print(f'FusionEngine: {version_info.engine_version_str}')
                print(f'OS: {version_info.os_version_str}')
                print(f'GNSS Receiver: {version_info.rx_version_str}')

        # Update the GNSS receiver.
        if gnss_bin_fd is not None:
            if (version_info is not None and
                version_info.rx_version_str == info_json.get('gnss_receiver', {}).get('version', "UNKNOWN") and
                not args.force):
                print('GNSS firmware already up to date (%s). Skipping.' % version_info.rx_version_str)
                gnss_bin_fd = None
            else:
                print('Upgrading GNSS firmware...')
                if not Upgrade(ser, gnss_bin_fd, UpgradeType.GNSS, should_send_reboot,
                               wait_for_reboot=app_bin_fd is not None):
                    sys.exit(2)

        # Update the application software.
        if app_bin_fd is not None:
            # If we did a GNSS update above, print a line break to separate the print statements for this update.
            if gnss_bin_fd is not None:
                print('')

            if (version_info is not None and
                version_info.engine_version_str == info_json.get('fusion_engine', {}).get('version', "UNKNOWN") and
                not args.force):
                print('Application software already up to date (%s). Skipping.' % version_info.engine_version_str)
            else:
                print('Upgrading application software...')
                if not Upgrade(ser, app_bin_fd, UpgradeType.APP, should_send_reboot):
                    sys.exit(2)


if __name__ == '__main__':
    main()
