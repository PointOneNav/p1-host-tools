import time
from threading import Event, Lock
from typing import BinaryIO, Optional, Union

import serial.threaded
from fusion_engine_client.messages import *
from fusion_engine_client.parsers import (FusionEngineDecoder,
                                          FusionEngineEncoder)
from serial import Serial

import p1_runner.trace as logging
from p1_runner.log_manager import LogManager
from p1_runner.nmea_framer import NMEAFramer


logger = logging.getLogger('point_one.device_interface')

RESPONSE_TIMEOUT = 5
RX_BYTE_TIMEOUT = 0.1
REBOOT_MAX_START_TIME = 7
REBOOT_MIN_TIME = 1
REBOOT_MAX_TIME = 5
MAX_DATA_BUFFER_SIZE = 10 * 1024 * 1024
DATA_BUFFER_DROP_SIZE = 1 * 1024 * 1024


class DeviceInterface(serial.threaded.Protocol):
    '''!
    @brief Class to simplify command and configuration communication with a device over serial.

    In order to use this class to receive data from the device, @ref start_rx_thread() must be called before anything else.
    '''
    def __init__(self, serial_out: Serial, serial_in: Optional[Serial] = None,
                 rx_byte_timeout=RX_BYTE_TIMEOUT, serial_rx_log: Optional[Union[LogManager, BinaryIO]] = None):
        '''!
        @param serial_out An open serial port class to use for sending data.
        @param serial_in An open serial port class to use for receiving data. If not set, use the same port as serial_out.
        @param rx_byte_timeout The timeout for waiting for a single byte of data from the device.
        @param serial_rx_log If set, all received data will be logged to this object.
        '''
        self.serial_out = serial_out
        self.serial_in = serial_in if serial_in is not None else serial_out
        self.rx_byte_timeout = rx_byte_timeout
        # size of UserConfig is 2048 bytes
        self.fe_decoder = FusionEngineDecoder(2080, warn_on_unrecognized=False, return_bytes=True)
        self.fe_encoder = FusionEngineEncoder()
        self.nmea_framer = NMEAFramer()
        self.serial_rx_logger = serial_rx_log
        self.data_buffer = b''
        # This event indicates that a byte or more is available in the data_buffer.
        self.data_event = Event()
        # This lock synchronizes writes to the data_buffer.
        self.data_lock = Lock()
        self.rx_thread = None

    # For serial.threaded.ReaderThread call.
    def __call__(self):
        return self

    def start_rx_thread(self):
        self.rx_thread = serial.threaded.ReaderThread(self.serial_in, self)
        self.rx_thread.start()

    def stop_rx_thread(self):
        if self.rx_thread:
            self.rx_thread.stop()
            self.rx_thread = None

    # The callback used in the self.rx_thread when serial data is received.
    def data_received(self, data):
        logger.trace(f'RX thread got data. [size={len(data)} B]')
        self.data_lock.acquire()
        self.data_buffer += data
        if len(data) > MAX_DATA_BUFFER_SIZE:
            logger.error(
                'Serial RX buffer full. Dropping oldest data. [buffer_size=%d B, dropping=%d B]', MAX_DATA_BUFFER_SIZE,
                DATA_BUFFER_DROP_SIZE)
            self.data_buffer = self.data_buffer[DATA_BUFFER_DROP_SIZE:]
        self.data_event.set()
        self.data_lock.release()

    def set_config(self, config_object, save=False, revert=False, interface: Optional[InterfaceID] = None):
        config_set_cmd = SetConfigMessage()
        if save:
            config_set_cmd.flags |= SetConfigMessage.FLAG_APPLY_AND_SAVE
        if revert:
            config_set_cmd.flags |= SetConfigMessage.FLAG_REVERT_TO_DEFAULT
        config_set_cmd.interface = interface
        config_set_cmd.config_object = config_object
        message = self.fe_encoder.encode_message(config_set_cmd)
        logger.debug('Sending config to device. [size=%d B]' % len(message))
        self._send(message)

    def send_save(self, action: SaveAction = SaveAction.SAVE):
        apply_cmd = SaveConfigMessage(action)
        message = self.fe_encoder.encode_message(apply_cmd)
        logger.debug('Saving config. [size=%d B]' % len(message))
        self._send(message)

    def get_config(self, source: ConfigurationSource, config: Union[ConfigType, InterfaceConfigSubmessage]):
        req_cmd = GetConfigMessage()
        if isinstance(config, InterfaceConfigSubmessage):
            req_cmd.interface_header = config
        else:
            req_cmd.config_type = config
        req_cmd.request_source = source
        message = self.fe_encoder.encode_message(req_cmd)
        logger.debug('Requesting config. [size=%d B]' % len(message))
        self._send(message)

    def get_message_rate(self, source: ConfigurationSource, config_object):
        config_object.insert(2, source)
        req_cmd = GetMessageRate(*config_object)
        message = self.fe_encoder.encode_message(req_cmd)
        logger.debug('Querying message rate. [size=%d B]' % len(message))
        self._send(message)

    def set_message_rate(self, config_object):
        _, protocol, message_id, rate, flags = config_object
        if ((protocol == ProtocolType.ALL or message_id == ALL_MESSAGES_ID) and rate != MessageRate.OFF and
                (flags & SetMessageRate.FLAG_INCLUDE_DISABLED_MESSAGES) == 0):
            logger.warning(
                'Warning: This command will NOT enable messages that are currently disabled. Rerun with '
                '"--include-disabled" to enable all messages.')

        config_set_cmd = SetMessageRate(*config_object)
        message = self.fe_encoder.encode_message(config_set_cmd)

        logger.debug('Sending message rate config to device. [size=%d B]' % len(message))
        self._send(message)

    def send_message(self, message: Union[MessagePayload, str]):
        if isinstance(message, MessagePayload):
            encoded_data = self.fe_encoder.encode_message(message)
            logger.debug('Sending %s message. [size=%d B]' % (repr(message), len(encoded_data)))
            self._send(encoded_data)
        else:
            if message[0] != '$':
                message = '$' + message
            message += '*%02X' % NMEAFramer._calculate_checksum(message)
            encoded_data = (message + '\r\n').encode('utf8')
            logger.debug('Sending NMEA message. [%s (%d B)]' % (message.rstrip(), len(encoded_data)))
            self._send(encoded_data)

    # Note, this will only work properly if the interface has at least one
    # periodic message enabled in both the active and saved configuration. This
    # could be done a little more simply if it just looked for sequence number
    # resets, but that would require an enabled FE message.
    def wait_for_reboot(self, data_stop_timeout=REBOOT_MAX_START_TIME, data_restart_timeout=REBOOT_MAX_TIME):
        start_time = time.time()
        reboot_started = False
        reboot_finished = False
        self.flush_serial_rx()
        logger.debug("Waiting for data to stop.")
        while not reboot_started and time.time() - start_time < data_stop_timeout:
            data = self._read(1, REBOOT_MIN_TIME)
            reboot_started = len(data) == 0
        if reboot_started:
            # Since device reset, expect sequence number to reset.
            self.fe_decoder._last_sequence_number = 0
            logger.debug("Waiting for data to resume.")
            data = self._read(1, data_restart_timeout)
            reboot_finished = len(data) > 0
            if not reboot_finished:
                logger.warning("Data didn't resume after reboot.")
        else:
            logger.warning('No reboot start detected.')

        return reboot_started and reboot_finished

    def flush_serial_rx(self):
        self.data_lock.acquire()
        in_waiting = len(self.data_buffer)
        self.data_lock.release()
        logger.debug('Flushing data in buffer. [size=%d B]' % in_waiting)
        self._read(in_waiting)

    def _read(self, size: int, timeout=RESPONSE_TIMEOUT) -> bytes:
        if self.rx_thread is None:
            raise RuntimeError('Reading DeviceInterface without calling "start_rx_thread".')
        data = b''
        start_time = time.time()
        while size > 0 and time.time() - start_time < timeout:
            logger.trace(f'Buffered {len(self.data_buffer)} B.')
            if not self.data_event.wait(self.rx_byte_timeout):
                logger.debug('Timed out waiting for byte to be added to buffer.')
                continue
            self.data_lock.acquire()

            if len(self.data_buffer) <= size:
                data += self.data_buffer
                size -= len(self.data_buffer)
                self.data_buffer = b''
                self.data_event.clear()
            else:
                data += self.data_buffer[:size]
                self.data_buffer = self.data_buffer[size:]
                size = 0

            self.data_lock.release()
        if self.serial_rx_logger:
            self.serial_rx_logger.write(data)
        return data

    def wait_for_message(self, msg_type, response_timeout=RESPONSE_TIMEOUT):
        if isinstance(msg_type, MessageType):
            return self._wait_for_fe_message(msg_type, response_timeout)
        else:
            return self._wait_for_nmea_message(msg_type, response_timeout)

    def _wait_for_fe_message(self, msg_type, response_timeout):
        start_time = time.time()
        while True:
            msgs = self.fe_decoder.on_data(self._read(1))
            for msg in msgs:
                if msg[0].message_type == msg_type:
                    logger.debug('Response: %s', str(msg[1]))
                    logger.debug(' '.join('%02x' % b for b in msg[2]))
                    return msg[1]
            if time.time() - start_time > response_timeout:
                return None

    def _wait_for_nmea_message(self, msg_type, response_timeout):
        if msg_type[0] != '$':
            msg_type = '$' + msg_type

        start_time = time.time()
        while True:
            msgs = self.nmea_framer.on_data(self._read(1))
            for msg in msgs:
                if msg.startswith(msg_type):
                    msg = msg.rstrip()
                    logger.debug('Response: %s', msg)
                    return msg
            if time.time() - start_time > response_timeout:
                return None

    def _send(self, message):
        logger.debug(' '.join('%02x' % b for b in message))
        self.serial_out.write(message)
