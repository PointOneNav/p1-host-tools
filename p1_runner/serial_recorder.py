from datetime import datetime, timezone, timedelta
import threading
import traceback

import serial

from . import trace as logging


class SerialRecorder(threading.Thread):
    logger = logging.getLogger('point_one.p1_runner.external_serial_recorder')

    def __init__(self, device_port=None, device_baud_rate=460800,
                 output_path=None):

        super().__init__(name='external_device')

        self.device_port = device_port
        self.device_baud_rate = device_baud_rate
        self.output_path = output_path

        self.device_serial = serial.Serial(baudrate=device_baud_rate, timeout=1.0)
        self.device_serial.port = device_port

        self.output_file = None

        self.shutdown_pending = threading.Event()

    def start(self):
        self.logger.info('Connecting to device using serial port %s.' % self.device_serial.port)
        self.logger.debug('Opening device serial port. [port=%s]' % self.device_serial.port)
        self.device_serial.open()

        self.start_time = datetime.now()
        self.last_status_time = self.start_time

        if self.output_path is not None and self.output_path != '':
            self.output_file = open(self.output_path, 'wb')

        self.shutdown_pending.clear()

        self.total_bytes_received = {
            'all': 0,
            'sent': 0
        }

        self.last_data_timeout_warning_time = None

        super().start()

    def stop(self):
        if self.is_alive():
            self.logger.debug('Shutting down external serial recorder.')
            self.shutdown_pending.set()
            if self.output_file is not None:
                self.output_file.close()

    def run(self):
        # Read all pending data.
        try:
            data = self.device_serial.read(self.device_serial.in_waiting or 1)
        except serial.SerialException as e:
            self.logger.error('Unexpected error reading from device:\r%s' % traceback.format_exc())
            return

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

    def _on_data(self, data):
        self.logger.trace('Received %d bytes from device.' % len(data), depth=2)
        self.total_bytes_received['all'] += len(data)

        # Print a data status update periodically.
        now = datetime.now()
        if (now - self.last_status_time).total_seconds() > 5.0:
            self.logger.debug(
                '%d bytes received. [elapsed=%.1f sec, sent=%d B]' %
                (self.total_bytes_received['all'],
                 self.total_bytes_received['sent'],
                 (now - self.start_time).total_seconds()))
            self.last_status_time = now

        if self.output_file is not None:
            self.output_file.write(data)

    def write(self, data):
        self.logger.trace('Sending %d bytes to the device.' % len(data))
        self.total_bytes_received['sent'] += len(data)
        self.device_serial.write(data)
