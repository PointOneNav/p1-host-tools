from datetime import datetime, timezone
from enum import IntEnum
import math
import os
import threading
import traceback

from fusion_engine_client.parsers import FusionEngineEncoder, FusionEngineDecoder
from fusion_engine_client.messages import *
from fusion_engine_client.utils.log import DEFAULT_LOG_BASE_DIR
from gpstime import gpstime
from pynmea2 import NMEASentence
import serial

from . import trace as logging
from .find_serial_device import find_serial_device, PortType
from .log_manager import LogManager
from .log_manifest import DeviceType
from .nmea_framer import NMEAFramer
from .ntrip_client import NTRIPClient
from .output_server import OutputServer
from .reference_generator import ReferenceGenerator
from .rtcm_framer import *
from .segger_rtt import SeggerRTTCapture
from .serial_recorder import SerialRecorder
from .wheel_tick_display import WheelTickDisplay


class State(IntEnum):
    WAITING_FOR_DATA = 1
    RESET_SENT = 2
    RESET_COMPLETE = 3


class P1Runner(threading.Thread):
    logger = logging.getLogger('point_one.p1_runner.runner')

    DEFAULT_DEVICE_ID = 'p1-lg69t'

    def __init__(self, device_id=None, reset_type='hot', wait_for_reset=True,
                 device_port='auto', device_baudrate=460800,
                 corrections_port=None, corrections_baudrate=460800,
                 external_port=None, external_baudrate=4608000, external_output_path=None, external_corrections=False,
                 logs_base_dir=DEFAULT_LOG_BASE_DIR, log_format='raw', log_created_cmd=None, log_timestamps=False,
                 output_tcp_address=None, output_websocket_address=None, output_type='fusion_engine',
                 reference_tcp_address=None, reference_format='p1log',
                 rtt_mode='none', rtt_port=None, rtt_kill_gdbserver=False,
                 wheel_tick_display_mode: str = None):
        if device_id is None:
            device_id = self.DEFAULT_DEVICE_ID

        super().__init__(name='p1_%s' % device_id)

        self.reference_tcp_address = reference_tcp_address
        self.reference_generator = None
        self.reference_filename = 'reference.csv' if reference_format == 'csv' else 'reference.p1log'

        if log_format is not None and log_format != 'none':
            files = ["console.txt", "runner.txt", "core"]
            if self.reference_tcp_address is not None:
                files.append(self.reference_filename)
            if external_port is not None and external_output_path is not None and external_output_path != '':
                files.append(external_output_path)
            logs_base_dir = os.path.expanduser(logs_base_dir)
            self.log_manager = LogManager(
                device_id=device_id, logs_base_dir=logs_base_dir, files=files, log_extension="." + log_format,
                log_created_cmd=log_created_cmd, log_timestamps=log_timestamps)

            if log_format == 'nmea' or log_format == 'p1log':
                self.log_format = log_format
            else:
                self.log_format = 'all'
        else:
            self.log_manager = None
            self.log_format = None

        self.external_port = external_port
        self.external_baudrate = external_baudrate
        self.external_output_path = external_output_path

        self.external_serial_recorder = None
        self.external_corrections = external_corrections

        # Connect to the requested serial port. If device_port is 'auto', detect the port automatically. Otherwise,
        # connect to the specified device port (even if it is not the Standard port).
        #
        # If a separate corrections port was specified, connect to that. Otherwise, use the same serial port for data
        # logging (device) and sending corrections data. corrections_port == 'auto' implies the user doesn't care which
        # port we use, so the best port is still the device port, rather than opening a second port.
        #
        # Note: We intentionally do not pass the 'port' argument to the constructor so the serial ports do not open
        # automatically. We'll open them in start() later.
        device_port = find_serial_device(port_name=device_port, port_type=PortType.STANDARD)
        self.device_serial = serial.Serial(baudrate=device_baudrate, timeout=1.0)
        self.device_serial.port = device_port

        if corrections_port is None or corrections_port == device_port or corrections_port == 'auto':
            self.corrections_serial = self.device_serial
        else:
            corrections_port = find_serial_device(port_name=corrections_port, port_type=PortType.ENHANCED)
            self.corrections_serial = serial.Serial(baudrate=corrections_baudrate, timeout=1.0)
            self.corrections_serial.port = corrections_port

        self.ntrip_client = None
        self.ntrip_position_override = None
        self.last_ntrip_position_update = None
        self.nmea_framer = NMEAFramer()

        self.rtcm_framer = RTCMFramer()

        self.rtt_mode = rtt_mode
        self.rtt_telnet_port = rtt_port
        self.rtt_kill_gdbserver = rtt_kill_gdbserver
        self.rtt_client = None

        self.reset_type = reset_type
        self.wait_for_reset = wait_for_reset
        self.state = None

        self.wheel_tick_display = None
        if wheel_tick_display_mode is not None:
            # When wheel tick display is enabled, we suppress the normal INFO prints from this class. If debugging is
            # enabled, however, we will not do that since the debug prints might contain important details.
            if self.logger.getEffectiveLevel() == logging.INFO:
                self.logger.setLevel(logging.WARNING)

            # GUI mode uses direct print() calls instead of logger.info() calls since the latter does not have an easy
            # way to suppress line breaks (needed for screen clearing).
            if wheel_tick_display_mode == 'gui' and self.logger.isEnabledFor(logging.DEBUG):
                self.logger.warning('Wheel tick GUI mode not supported when debug is enabled.')
                wheel_tick_display_mode = 'text'

            self.wheel_tick_display = WheelTickDisplay(device_serial=self.device_serial,
                                                       display_mode=wheel_tick_display_mode)

        if output_tcp_address is not None or output_websocket_address is not None:
            self.output_server = OutputServer(tcp_address=output_tcp_address,
                                              websocket_address=output_websocket_address,
                                              legacy_nmea=output_type == 'legacy_nmea')
            if output_type == 'legacy_nmea':
                self.output_type = 'nmea'
            else:
                self.output_type = output_type
        else:
            self.output_server = None
            self.output_type = None

        # For sending a warning message in the event that data is arriving from multiple sources.
        self.external_data_warning_sent = False

        self.shutdown_pending = threading.Event()

        self.total_bytes_received = {
            'all': 0,
            'corrections': 0,
            'fe': 0,
            'nmea': 0
        }

        self.fe_positions_received = 0
        self.nmea_positions_received = 0

        self.start_time = None
        self.last_status_time = None
        self.last_text_ui_p1_time = None
        self.last_ntrip_position_update = None

        self.last_data_timeout_warning_time = None
        self.last_reset_timeout_warning_time = None
        self.last_missing_fe_warning_time = None
        self.updated_manifest_with_version = False

    def start(self):
        self.rtcm_framer.reset()
        self.state = State.WAITING_FOR_DATA if self.reset_type != 'none' else State.RESET_COMPLETE

        self.logger.info('Connecting to device using serial port %s.' % self.device_serial.port)
        self.logger.debug('Opening device serial port. [port=%s]' % self.device_serial.port)
        self.device_serial.open()
        if self.device_serial.port != self.corrections_serial.port:
            self.logger.debug('Opening corrections serial port. [port=%s]' % self.corrections_serial.port)
            self.corrections_serial.open()
        else:
            self.logger.debug('Sending corrections on device serial port. [port=%s]' % self.device_serial.port)
            self.corrections_serial = self.device_serial

        if self.output_server is not None:
            self.logger.debug('Starting output server.')
            self.output_server.start()
            self.output_server.register_incoming_data_callback(self._handle_external_message)

        if self.log_manager is not None:
            self.logger.debug('Starting log manager.')
            self.log_manager.start()

            if self.reference_tcp_address is not None:
                self.logger.debug('Starting reference file generator.')
                self.reference_generator = ReferenceGenerator(
                    hostname=self.reference_tcp_address[0], port=self.reference_tcp_address[1],
                    path=self.log_manager.get_abs_file_path(self.reference_filename))
                self.reference_generator.start()
        else:
            self.logger.debug('Logging disabled.')

        if self.rtt_mode != 'none':
            print_output = self.rtt_mode in ('print', 'all')
            log_output = self.rtt_mode in ('log', 'all')
            if log_output and self.log_manager is not None:
                console_path = self.log_manager.get_abs_file_path('console.txt')
            else:
                console_path = None

            self.logger.debug('Starting RTT console capture.')
            self.rtt_client = SeggerRTTCapture(output_path=console_path, print_output=print_output,
                                               telnet_port=self.rtt_telnet_port,
                                               force_kill_gdbserver=self.rtt_kill_gdbserver)
            self.rtt_client.start()

        if self.ntrip_client is not None:
            self.logger.debug('Starting NTRIP corrections stream.')
            self.ntrip_client.start()

        if self.state == State.RESET_COMPLETE:
            self.logger.debug('Device reset disabled. Data will be recorded immediately.')

        self.logger.debug('Listening for incoming data.')

        self.start_time = datetime.now()
        self.last_status_time = self.start_time

        self.rtcm_framer.reset()
        self.nmea_framer.reset()

        self.fe_encoder = FusionEngineEncoder()
        self.fe_decoder = FusionEngineDecoder(max_payload_len_bytes=4096, warn_on_unrecognized=False, return_bytes=True)
        self.fe_decoder.add_callback(PoseMessage.MESSAGE_TYPE, self._handle_pose)
        self.fe_decoder.add_callback(CommandResponseMessage.MESSAGE_TYPE, self._handle_cmd_response)
        self.fe_decoder.add_callback(CalibrationStatus.MESSAGE_TYPE, self._handle_calibration_status)
        self.fe_decoder.add_callback(VersionInfoMessage.MESSAGE_TYPE, self._handle_version)

        if self.wheel_tick_display:
            self.fe_decoder.add_callback(None, self.wheel_tick_display.handle_message)

        if self.external_port is not None:
            if self.log_manager is not None and self.external_output_path is not None and \
               self.external_output_path != '':
                self.external_output_path = self.log_manager.get_abs_file_path(self.external_output_path)

            self.external_serial_recorder = SerialRecorder(device_port=self.external_port,
                                                           device_baud_rate=self.external_baudrate,
                                                           output_path=self.external_output_path)
            self.external_serial_recorder.start()

        self.shutdown_pending.clear()
        super().start()

    def stop(self):
        if self.is_alive():
            self.logger.debug('Shutting down runner.')
            self.shutdown_pending.set()

            if self.output_server is not None:
                self.output_server.stop()

            if self.ntrip_client is not None:
                self.ntrip_client.stop()

            if self.rtt_client is not None:
                self.rtt_client.stop()

            if self.log_manager is not None:
                self.log_manager.stop()

            if self.reference_generator is not None:
                self.logger.debug('Stopping reference file generator.')
                self.reference_generator.stop()

            if self.external_serial_recorder is not None:
                self.external_serial_recorder.stop()

    def join(self, timeout=None):
        super().join(timeout)
        if self.output_server is not None:
            self.output_server.join()
        if self.ntrip_client is not None:
            self.ntrip_client.join(timeout)
        if self.rtt_client is not None:
            self.rtt_client.join(timeout)
        if self.log_manager is not None:
            self.log_manager.join(timeout)
        if self.reference_generator is not None:
            self.reference_generator.join(timeout)

        self.device_serial.close()
        if self.corrections_serial.is_open:
            self.corrections_serial.close()

    def connect_to_ntrip(self, url=None, mountpoint=None, username=None, password=None, version=2):
        if self.ntrip_client is not None:
            self.ntrip_client.stop()
            self.ntrip_client.join()

        self.logger.debug('Configuring NTRIP corrections stream. [url=%s, ntrip_version=%d, mountpoint=%s]' %
                          (url, version, mountpoint))
        self.ntrip_client = NTRIPClient(url=url, mountpoint=mountpoint, username=username, password=password,
                                        data_callback=self._on_corrections, version=version)
        if self.is_alive():
            self.ntrip_client.start()

    def set_ntrip_position_override(self, lla_deg):
        self.logger.debug('Overriding NTRIP position. [%.8f, %.8f, %.2f]' % tuple(lla_deg))
        self.ntrip_position_override = lla_deg
        if self.ntrip_client is not None and self.ntrip_client.is_connected():
            if self.ntrip_client.send_position(self.ntrip_position_override):
                self.last_ntrip_position_update = datetime.now(tz=timezone.utc)

    def run(self):
        while not self.shutdown_pending.is_set():
            # Read all pending data, or block until at least 1 byte comes in.
            try:
                data = self.device_serial.read(self.device_serial.in_waiting or 1)
            except serial.SerialException as e:
                self.logger.error('Unexpected error reading from device:\r%s' % traceback.format_exc())
                self.logger.error('Application exiting.')
                sys.exit(1)

            if len(data) > 0:
                self.last_data_timeout_warning_time = None
                self._on_data(data)
            else:
                now = datetime.now()
                if self.last_data_timeout_warning_time is None:
                    self.last_data_timeout_warning_time = now - timedelta(seconds=self.device_serial.timeout)
                elif (now - self.last_data_timeout_warning_time).total_seconds() > 5.0:
                    self.logger.warning("Timed out waiting for data on %s." % self.device_serial.port)
                    self.last_data_timeout_warning_time = now

            if self.external_serial_recorder is not None:
                self.external_serial_recorder.run()

    def _on_data(self, data):
        self.logger.trace('Received %d bytes from device.' % len(data), depth=2)

        # If we just started and this is the first data we've gotten, we can now assume the device is connected. Issue
        # a reset request, forcing the device to reset so that we receive and log 100% of the data the device uses.
        if self.state == State.WAITING_FOR_DATA:
            self._send_reset()
            self.state = State.RESET_SENT if self.wait_for_reset else State.RESET_COMPLETE
            return
        elif self.state == State.RESET_SENT:
            while not self.state == State.RESET_COMPLETE:
                # Pass one byte at a time into the FusionEngine decoder. This
                # will pull out FusionEngine messages from the mixed data coming
                # over the serial port. When a reset response message is decoded
                # it will trigger the callback function @ref
                # _handle_cmd_response(). This function updates the state to
                # escape this loop with any data remaining.
                self.fe_decoder.on_data(data[0])
                data = data[1:]
                if len(data) == 0:
                    break

            # If we are still waiting for a reset response, return and skip all data processing below.
            if not self.state == State.RESET_COMPLETE:
                # Warn if we don't get the reset response quickly and send the request again.
                if (datetime.now() - self.last_reset_timeout_warning_time).total_seconds() > 5.0:
                    self.logger.warning("Reset response timed out. Resending reset request.")
                    self._send_reset()
                return

        # If we get this far, the device reset is now complete and we can begin processing data from here down.

        # Check if this is the first data processed.
        first_data = self.total_bytes_received['all'] == 0
        self.total_bytes_received['all'] += len(data)

        # Now that the device is operating, if wheel tick display mode is enabled, query the configuration.
        if self.wheel_tick_display and first_data:
            self.wheel_tick_display.query_config()

        # If we are logging data and we are _not_ using .p1log format (i.e., recording only FusionEngine messages),
        # store the data now before attempting to process it further.
        #
        # Similarly, if we are relaying all incoming data to TCP, do so now.
        if self.log_format == 'all':
            self.log_manager.write(data)

        if self.output_type == 'all':
            self.output_server.send(data)

        # Run the data through the FusionEngine decoder, which will call the registered _handle_*() functions as
        # messages arrive.
        #
        # If we are logging a .p1log file, store the framed binary data, ignoring any non-FusionEngine content
        # surrounding it.
        #
        # Similarly, if we are forwarding incoming FusionEngine data to TCP, do so now.
        results = self.fe_decoder.on_data(data)
        for entry in results:
            self.logger.trace('Received FusionEngine %s message. [size=%d B]' %
                              (str(entry[0].message_type), len(entry[2])))
            self.total_bytes_received['fe'] += len(entry[2])
            if self.log_format == 'p1log':
                self.log_manager.write(entry[2])
            if self.output_type == 'fusion_engine':
                self.output_server.send(entry[2])

        # Run the data through the RTCM framer and print out incoming message IDs. In the future, we may handle some
        # incoming message types (e.g., Point One diagnostic messages).
        if self.logger.isEnabledFor(logging.TRACE):
            results = self.rtcm_framer.on_data(data, return_size=True)
            for entry in results:
                self.logger.trace('Received RTCM %d message. [size=%d B]' %
                                  (entry['message'].message_id, entry['size']))
                if isinstance(entry['message'].payload, bytes):
                    payload_str = ''.join(''.join(['\\x%02X' % b for b in entry['message'].payload]))
                else:
                    payload_str = repr(entry['message'].payload)
                self.logger.trace('Payload: %s' % payload_str, depth=2)

        # Frame the NMEA data, then log/forward it if requested, and print out GGA for debugging.
        for msg in self.nmea_framer.on_data(data):
            self.logger.trace('Received NMEA message: %s' % msg.strip())
            self.total_bytes_received['nmea'] += len(msg)

            # If we are logging NMEA or forwarding incoming NMEA data to TCP, do so now.
            if self.log_format == 'nmea':
                self.log_manager.write(msg)
            if self.output_type == 'nmea':
                self.output_server.send(msg)

            if msg[0] == '$' and msg[3:7] == 'GGA,':
                # Print the GGA string for debug purposes.
                self.logger.trace(msg.strip())

                # If we've received multiple NMEA positions but have not seen FusionEngine positions, warn the user.
                self.nmea_positions_received += 1
                if self.nmea_positions_received > 10 and self.fe_positions_received == 0:
                    now = datetime.now(tz=timezone.utc)
                    if (self.last_missing_fe_warning_time is None or
                        (now - self.last_missing_fe_warning_time).total_seconds() >= 30.0):
                        self.logger.warning("""
////////////////////////////////////////////////////////////////////////////////
FusionEngine data not detected on %s.

Are you using the correct UART/COM port (--device-port)?
////////////////////////////////////////////////////////////////////////////////
""" % self.device_serial.port)
                        self.last_missing_fe_warning_time = now

        # Print a data status update periodically.
        now = datetime.now()
        if (now - self.last_status_time).total_seconds() > 5.0:
            self.logger.info(
                '%d bytes received. [# epochs=%d, elapsed=%.1f sec, fusion_engine=%d B, nmea=%d B, corrections=%d B]' %
                (self.total_bytes_received['all'], self.fe_positions_received, (now - self.start_time).total_seconds(),
                 self.total_bytes_received['fe'], self.total_bytes_received['nmea'],
                 self.total_bytes_received['corrections']))
            self.last_status_time = now

    def _on_corrections(self, data):
        self.logger.trace('Received %d bytes from NTRIP stream.' % len(data))

        if self.external_serial_recorder is not None and self.external_corrections:
            self.external_serial_recorder.write(data)

        self.total_bytes_received['corrections'] += len(data)
        if self.corrections_serial.is_open:
            if self.state == State.RESET_COMPLETE:
                self.corrections_serial.write(data)
            else:
                self.logger.trace('Waiting for reset. Discarding corrections data.')

    def _send_reset(self):
        reset_cmd = ResetRequest()

        if self.reset_type == 'hot':
            reset_cmd.reset_mask = ResetRequest.HOT_START
        elif self.reset_type == 'warm':
            reset_cmd.reset_mask = ResetRequest.WARM_START
        elif self.reset_type == 'pvt':
            reset_cmd.reset_mask = ResetRequest.PVT_RESET
        elif self.reset_type == 'diag':
            reset_cmd.reset_mask = ResetRequest.DIAGNOSTIC_LOG_RESET
        elif self.reset_type == 'cold':
            reset_cmd.reset_mask = ResetRequest.COLD_START
        else:
            raise ValueError("Unsupported reset type '%s'." % self.reset_type)

        message = self.fe_encoder.encode_message(reset_cmd)

        self.logger.info('Issuing reset request (%s start) to the device.' % self.reset_type)
        self.device_serial.write(message)
        self.last_reset_timeout_warning_time = datetime.now()

    def _handle_pose(self, header: MessageHeader, response_payload: PoseMessage, *args):
        if not self.state == State.RESET_COMPLETE:
            return

        self.fe_positions_received += 1

        # Construct a text UI string to be printed to the console.
        output_str = ''

        if response_payload.gps_time:
            gps_sec = float(response_payload.gps_time)

            # strftime() doesn't support specifying the precision of the fractional seconds, it just prints 6 values for
            # microseconds all the time. We only want to print out 2 decimal places for the second value/GPS TOW. Round
            # to the nearest 0.01 seconds, then we'll strip off the last 4 chars below.
            gps_sec = round(gps_sec * 100) / 100

            gps_time = gpstime.fromgps(gps_sec)
            seconds_per_week = 7 * 24 * 3600
            week = math.floor(gps_sec / seconds_per_week)
            tow_sec = gps_sec - week * seconds_per_week
            output_str += '%s UTC (GPS %d:%.2f, ' % (gps_time.strftime('%Y/%m/%d %H:%M:%S.%f')[:-4], week, tow_sec)
        else:
            gps_sec = None

        p1_time_sec = float(response_payload.p1_time)
        output_str += 'P1 %.2f sec' % p1_time_sec
        if gps_sec is not None:
            output_str += ')'

        output_str += ' - [LLA=%.8f, %.8f, %.2f] [Type=%s (%d)]' %\
                      (*response_payload.lla_deg, response_payload.solution_type, response_payload.solution_type)

        # If we have GPS time and this is a second boundary, print now. Otherwise, if we don't have GPS time, print 1
        # Hz based on P1 time.
        if gps_sec is not None:
            frac = gps_sec % 1
            print_now = (frac < 0.05 or (1 - frac) < 0.05)
        else:
            print_now = self.last_text_ui_p1_time is None or (p1_time_sec - self.last_text_ui_p1_time) >= 1.0

        if print_now:
            self.logger.info(output_str)
            self.last_text_ui_p1_time = p1_time_sec
        else:
            self.logger.debug(output_str)

        # Forward the position to the NTRIP server every 60 seconds.
        if (self.ntrip_client is not None and
            (response_payload.solution_type != SolutionType.Invalid or self.ntrip_position_override is not None)):
            now = datetime.now(tz=timezone.utc)
            if (self.last_ntrip_position_update is None or
                (now - self.last_ntrip_position_update).total_seconds() >= 60.0):
                if self.ntrip_position_override is not None:
                    if self.ntrip_client.send_position(self.ntrip_position_override):
                        self.last_ntrip_position_update = now
                else:
                    if self.ntrip_client.send_position(response_payload.lla_deg):
                        self.last_ntrip_position_update = now

    def _handle_cmd_response(self, header: MessageHeader, response_payload: CommandResponseMessage, *args):
        if response_payload.response == Response.OK:
            self.logger.info('Device reset complete. Starting data logging.')
            self.state = State.RESET_COMPLETE
        else:
            self.logger.error('Reset rejected by device. Logging all future data for debugging purposes.')
            self.state = State.RESET_COMPLETE

    def _handle_calibration_status(self, header: MessageHeader, status: CalibrationStatus, *args):
        if not self.state == State.RESET_COMPLETE:
            return

        self.logger.info(
            'Calibration: stage=%s, gyro=%.1f%%, accel=%.1f%%, mounting_angles=%.1f%%' %
            (str(status.calibration_stage), status.gyro_bias_percent_complete, status.accel_bias_percent_complete,
            status.mounting_angle_percent_complete))
        self.logger.info(
            '           : ypr=(%.1f, %.1f, %.1f) deg, ypr_std=(%.1f, %.1f, %.1f) deg, dist=%.1f km' %
            (*status.ypr_deg, *status.ypr_std_dev_deg, status.travel_distance_m * 1e-3))

    def _handle_version(self, header: MessageHeader, version: VersionInfoMessage, *args):
        if self.updated_manifest_with_version:
            return

        sw_version = version.engine_version_str
        self.logger.info('Detected FusionEngine software version: %s', sw_version)

        if sw_version.startswith('lg69t-ap'):
            device_type = DeviceType.LG69T_AP
        elif sw_version.startswith('lg69t-am'):
            device_type = DeviceType.LG69T_AM
        elif sw_version.startswith('lg69t-ah'):
            device_type = DeviceType.LG69T_AH
        else:
            device_type = DeviceType.UNKNOWN
            self.logger.warning('Could not deduce device type from version string.')

        if self.log_manager is not None:
            self.log_manager.update_manifest([
                ('device_type', device_type),
                ('sw_version', sw_version),
            ])

        self.updated_manifest_with_version = True

    def _handle_external_message(self, msg):
        # This is meant to handle data sent from the user (such as from P1 Desktop) and forwarded to the device; the
        # types of data currently intended to be sent are commands and corrections.
        #
        # Note that no assumptions are made about the data, so it is not being framed or interleaved with corrections
        # that may be coming from elsewhere. So, there is an implicit assumption that the user, if sending data from
        # a source such as P1 Desktop, is configuring to send data ONLY from that source.
        if self.ntrip_client is not None and not self.external_data_warning_sent:
            self.logger.error("""
Detected data incoming from external source, whereas NTRIP client is configured. If corrections are being sent from
both sources, this may lead to conflicting data and undefined behavior.
""")
            self.external_data_warning_sent = True

        # pyserial requires a bytes object as input, even for ASCII data.
        if isinstance(msg, str):
            msg = msg.encode('utf-8')

        self.device_serial.write(msg)
