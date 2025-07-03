import time
from typing import Optional, Union

from fusion_engine_client.messages import *
from fusion_engine_client.parsers import (FusionEngineDecoder,
                                          FusionEngineEncoder)
from fusion_engine_client.parsers.decoder import MessageWithBytesTuple

import p1_runner.trace as logging
from p1_runner.data_source import RESPONSE_TIMEOUT, DataSource
from p1_runner.nmea_framer import NMEAFramer

logger = logging.getLogger('point_one.device_interface')

REBOOT_MAX_START_TIME = 7
REBOOT_MIN_TIME = 1
REBOOT_MAX_TIME = 5

MAX_FE_MSG_SIZE = 16 * 1024


class DeviceInterface:
    '''!
    @brief Class to simplify command and configuration communication with a device over serial.

    In order to use this class to receive data from the device, @ref start_rx_thread() must be called before anything else.
    '''

    def __init__(self, data_source: DataSource):
        '''!
        @param serial_out An open serial port class to use for sending data.
        @param serial_in An open serial port class to use for receiving data. If not set, use the same port as serial_out.
        @param rx_byte_timeout The timeout for waiting for a single byte of data from the device.
        @param serial_rx_log If set, all received data will be logged to this object.
        '''
        self.data_source = data_source
        # size of UserConfig is 2048 bytes
        self.fe_decoder = FusionEngineDecoder(MAX_FE_MSG_SIZE, warn_on_unrecognized=False, return_bytes=True)
        self.fe_encoder = FusionEngineEncoder()
        self.nmea_framer = NMEAFramer()
        self.buffer = bytes()

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
        self.data_source.write(message)

    def send_save(self, action: SaveAction = SaveAction.SAVE):
        apply_cmd = SaveConfigMessage(action)
        message = self.fe_encoder.encode_message(apply_cmd)
        logger.debug('Saving config. [size=%d B]' % len(message))
        self.data_source.write(message)

    def get_config(self, source: ConfigurationSource, config: Union[ConfigType, InterfaceConfigSubmessage]):
        req_cmd = GetConfigMessage()
        if isinstance(config, InterfaceConfigSubmessage):
            req_cmd.interface_header = config
        else:
            req_cmd.config_type = config
        req_cmd.request_source = source
        message = self.fe_encoder.encode_message(req_cmd)
        logger.debug('Requesting config. [size=%d B]' % len(message))
        # We flush the serial RX buffer before we send the request in an attempt to avoid the response timing out as we
        # process the backlog of data. This only a concern when running on a lower CPU power system like a Raspi that
        # may not be able to keep up with the byte-by-byte processing for a high data rate interface.
        self.data_source.flush_rx()
        self.data_source.write(message)

    def get_message_rate(self, source: ConfigurationSource, config_object):
        config_object.insert(2, source)
        req_cmd = GetMessageRate(*config_object)
        message = self.fe_encoder.encode_message(req_cmd)
        logger.debug('Querying message rate. [size=%d B]' % len(message))
        # We flush the serial RX buffer before we send the request in an attempt to avoid the response timing out as we
        # process the backlog of data. This only a concern when running on a lower CPU power system like a Raspi that
        # may not be able to keep up with the byte-by-byte processing for a high data rate interface.
        self.data_source.flush_rx()
        self.data_source.write(message)

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
        self.data_source.write(message)

    def send_message(self, message: Union[MessagePayload, str]):
        if isinstance(message, MessagePayload):
            encoded_data = self.fe_encoder.encode_message(message)
            logger.debug('Sending %s message. [size=%d B]' % (repr(message), len(encoded_data)))
            self.data_source.write(encoded_data)
        else:
            if message[0] != '$':
                message = '$' + message
            message += '*%02X' % NMEAFramer._calculate_checksum(message)
            encoded_data = (message + '\r\n').encode('utf8')
            logger.debug('Sending NMEA message. [%s (%d B)]' % (message.rstrip(), len(encoded_data)))
            self.data_source.write(encoded_data)

    # Note, this will only work properly if the interface has at least one
    # periodic message enabled in both the active and saved configuration. This
    # could be done a little more simply if it just looked for sequence number
    # resets, but that would require an enabled FE message.
    def wait_for_reboot(self, data_stop_timeout=REBOOT_MAX_START_TIME, data_restart_timeout=REBOOT_MAX_TIME):
        start_time = time.time()
        reboot_started = False
        reboot_finished = False
        self.data_source.flush_rx()
        logger.debug("Waiting for data to stop.")
        while not reboot_started and time.time() - start_time < data_stop_timeout:
            data = self.data_source.read(MAX_FE_MSG_SIZE, REBOOT_MIN_TIME, return_any=True)
            reboot_started = len(data) == 0
        if reboot_started:
            # Since device reset, expect sequence number to reset.
            self.fe_decoder._last_sequence_number = 0
            logger.debug("Waiting for data to resume.")
            data = self.data_source.read(MAX_FE_MSG_SIZE, data_restart_timeout, return_any=True)
            reboot_finished = len(data) > 0
            if not reboot_finished:
                logger.warning("Data didn't resume after reboot.")
        else:
            logger.warning('No reboot start detected.')

        return reboot_started and reboot_finished

    def _read_next_byte(self, timeout) -> Optional[int]:
        if len(self.buffer) == 0:
            self.buffer = self.data_source.read(MAX_FE_MSG_SIZE, timeout, return_any=True)

        if len(self.buffer) == 0:
            return None
        else:
            b = self.buffer[0]
            self.buffer = self.buffer[1:]
            return b

    def wait_for_any_fe_message(self, response_timeout=RESPONSE_TIMEOUT) -> List[MessageWithBytesTuple]:
        start_time = time.monotonic()
        elapsed = 0
        while elapsed <= response_timeout:
            time_remaining = response_timeout - elapsed
            b = self._read_next_byte(time_remaining)
            if b is not None:
                msgs = self.fe_decoder.on_data(b)
                if len(msgs) > 0:
                    return msgs  # type: ignore
            elapsed = time.monotonic() - start_time
        return []

    def poll_messages(self, read_buffer_size=MAX_FE_MSG_SIZE, response_timeout=0.0,
                      return_any=True) -> List[MessageWithBytesTuple]:
        if len(self.buffer) > 0:
            msgs = self.fe_decoder.on_data(self.buffer)
            self.buffer = bytes()
            if len(msgs) > 0:
                return msgs  # type: ignore
        return self.fe_decoder.on_data(self.data_source.read(
            read_buffer_size, response_timeout, return_any))  # type: ignore

    def wait_for_message(self, msg_type, response_timeout=RESPONSE_TIMEOUT):
        if isinstance(msg_type, MessageType):
            return self._wait_for_fe_message(msg_type, response_timeout)
        else:
            return self._wait_for_nmea_message(msg_type, response_timeout)

    def _wait_for_fe_message(self, msg_type, response_timeout):
        start_time = time.monotonic()
        elapsed = 0
        while elapsed <= response_timeout:
            time_remaining = response_timeout - elapsed
            b = self._read_next_byte(time_remaining)
            if b is not None:
                msgs = self.fe_decoder.on_data(b)
                for msg in msgs:
                    if msg[0].message_type == msg_type:
                        logger.debug('Response: %s', str(msg[1]))
                        logger.debug(' '.join('%02x' % b for b in msg[2]))
                        return msg[1]
            elapsed = time.monotonic() - start_time
        return None

    def _wait_for_nmea_message(self, msg_type, response_timeout):
        if msg_type[0] != '$':
            msg_type = '$' + msg_type

        start_time = time.monotonic()
        elapsed = 0
        while elapsed <= response_timeout:
            time_remaining = response_timeout - elapsed
            b = self._read_next_byte(time_remaining)
            if b is not None:
                msgs = self.nmea_framer.on_data(bytes([b]))
                for msg in msgs:
                    if msg.startswith(msg_type):
                        msg = msg.rstrip()
                        logger.debug('Response: %s', msg)
                        return msg
            elapsed = time.monotonic() - start_time
        return None
