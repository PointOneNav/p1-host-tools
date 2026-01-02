import select
import socket
import time
from abc import ABC, abstractmethod
from socket import SocketType
from threading import Event, Lock
from typing import BinaryIO, Optional, Union

import serial.threaded
from fusion_engine_client.utils.socket_timestamping import (
    enable_socket_timestamping, parse_timestamps_from_ancdata)
try:
    from serial import Serial
except (ImportError, AttributeError) as exc:
    import serial as _serial_module
    if hasattr(_serial_module, "Serial"):
        Serial = _serial_module.Serial
    else:
        raise ImportError(
            "PySerial is required (pip install pyserial). "
            "The 'serial' package on PyPI is not compatible."
        ) from exc

try:
    from websockets.sync.client import ClientConnection
except ImportError:
    class ClientConnection:
        pass

import p1_runner.trace as logging
from p1_runner.log_manager import LogManager

logger = logging.getLogger('point_one.data_source')


RESPONSE_TIMEOUT = 5.0
RX_BYTE_TIMEOUT = 0.1
MAX_DATA_BUFFER_SIZE = 10 * 1024 * 1024
DATA_BUFFER_DROP_SIZE = 1 * 1024 * 1024


class DataSource(ABC):
    """!
    @brief A bidirectional data connection to a device.

    This abstracts transport specific concerns and allows for logging of all received data.
    """

    def __init__(self) -> None:
        super().__init__()
        self.last_rx_data_posix_timestamp_sec = 0.0

    @abstractmethod
    def write(self, data: bytes):
        """!
        @brief Write data to the device.

        @param data The data to send. Block until the entire contents are sent.
        """
        pass

    @abstractmethod
    def read(self, size: int, timeout=RESPONSE_TIMEOUT, return_any: bool = False) -> bytes:
        """!
        @brief Read data from the device.

        @param size The maximum amount of data to read.
        @param timeout The max time in seconds this function should take before returning with less than `size` bytes.
        @param return_any Return as soon as any data is available even if it's less than size.

        @return The data read. If the read timed out, the length of the returned data will be less than `size`.
        """
        pass

    @abstractmethod
    def flush_rx(self):
        """!
        @brief Flush the data received by the host.

        The host OS will typically have data buffered beyond what the application has read. This call is to flush that
        data. This will ensure that any further data read was received after this call. This will also flush data that
        had been received to the log if present.
        """
        pass

    @abstractmethod
    def stop(self):
        """!
        The child class may need to use file handles or start threads as part of its functionality. This call will close
        any handles and stop and threads being used.
        """
        pass


class WebSocketDataSource(DataSource):
    """!
    @brief A class to abstract socket IO details for a connection to a device.

    For the most part this is just to make the access calls conform to the needed behaviors.
    """

    def __init__(
            self, socket_out: ClientConnection, socket_in: Optional[ClientConnection] = None,
            rx_log: Optional[Union[LogManager, BinaryIO]] = None):
        self.socket_out = socket_out
        self.socket_in = socket_in if socket_in is not None else socket_out

        self.rx_log = rx_log

    def write(self, data: bytes):
        self.socket_out.send(data)

    def read(self, size: int, timeout=RESPONSE_TIMEOUT, return_any: bool = False) -> bytes:
        try:
            data = self.socket_in.recv(timeout)
            self.last_rx_data_posix_timestamp_sec = time.time()
        except TimeoutError:
            data = b''
        if isinstance(data, str):
            data = data.encode('ascii')
        if self.rx_log:
            self.rx_log.write(data)
        return data

    def stop(self):
        self.flush_rx()
        if self.socket_in is not None:
            self.socket_in.close()
        if self.socket_out is not None and self.socket_out != self.socket_in:
            self.socket_out.close()

    def flush_rx(self):
        in_waiting = 0
        while True:
            data = self.read(1024, 0)
            if len(data) == 0:
                break
            in_waiting += len(data)
        logger.debug('Flushing data in buffer. [size=%d B]' % in_waiting)


class SocketDataSource(DataSource):
    """!
    @brief A class to abstract socket IO details for a connection to a device.

    For the most part this is just to make the access calls conform to the needed behaviors.
    """

    def __init__(
            self, socket_out: SocketType, socket_in: Optional[SocketType] = None,
            rx_log: Optional[Union[LogManager, BinaryIO]] = None):
        self.socket_out = socket_out
        self.socket_in = socket_in if socket_in is not None else socket_out
        if self.socket_in:
            # The socket buffer can be made large enough that we don't need a reader thread.
            self.socket_in.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, MAX_DATA_BUFFER_SIZE)
            self.socket_in.setblocking(False)
            enable_socket_timestamping(self.socket_in, enable_sw_timestamp=True,
                                       enable_hw_timestamp=False)  # type: ignore

        self.rx_log = rx_log

    def write(self, data: bytes):
        self.socket_out.sendall(data)

    def read(self, size: int, timeout=RESPONSE_TIMEOUT, return_any: bool = False) -> bytes:
        all_data = b''
        start_time = time.time()
        remaining = timeout
        while len(all_data) < size and remaining >= 0:
            ready = select.select([self.socket_in], [], [], remaining)
            if ready[0]:
                data, ancdata, _, _ = self.socket_in.recvmsg(size - len(all_data), 1024)
                kernel_ts, _, _ = parse_timestamps_from_ancdata(ancdata)
                if kernel_ts:
                    self.last_rx_data_posix_timestamp_sec = kernel_ts

                if self.rx_log:
                    if kernel_ts and isinstance(self.rx_log, LogManager):
                        # Timestamps are only logged if enabled in the LogManager. Otherwise, they are ignored.
                        self.rx_log.write_with_timestamp(data, kernel_ts)
                    else:
                        self.rx_log.write(data)
                all_data += data
                if return_any:
                    break
            else:
                break
            remaining = timeout - (time.time() - start_time)

        return all_data

    def stop(self):
        self.flush_rx()
        if self.socket_in is not None:
            self.socket_in.close()
        if self.socket_out is not None and self.socket_out != self.socket_in:
            self.socket_out.close()

    def flush_rx(self):
        in_waiting = 0
        while True:
            data = self.read(1024, 0)
            if len(data) == 0:
                break
            in_waiting += len(data)
        logger.debug('Flushing data in buffer. [size=%d B]' % in_waiting)


class SerialDataSource(DataSource, serial.threaded.Protocol):
    """!
    @brief A class to abstract serial port details for a connection to a device.

    The main complexity here is that serial ports on Linux only support a 4kB buffer. This means that at high data
    rates, the port must be read constantly to avoid dropping data. This class provides a thread to do that and store
    the data until it's actually needed.
    """

    def __init__(
            self, serial_out: Serial, serial_in: Optional[Serial] = None,
            rx_log: Optional[Union[LogManager, BinaryIO]] = None):
        self.serial_out = serial_out
        self.serial_in = serial_in if serial_in is not None else serial_out
        self.rx_log = rx_log
        self.data_buffer = b''
        # This event indicates that a byte or more is available in the data_buffer.
        self.data_event = Event()
        # This lock synchronizes writes to the data_buffer.
        self.data_lock = Lock()
        self.rx_thread = None

    def write(self, data: bytes):
        logger.debug(' '.join('%02x' % b for b in data))
        self.serial_out.write(data)

    def read(self, size: int, timeout=RESPONSE_TIMEOUT, return_any: bool = False) -> bytes:
        if self.rx_thread is None:
            raise RuntimeError('Reading DeviceInterface without calling "start_rx_thread".')
        data = b''
        start_time = time.monotonic()
        now = start_time
        while size > 0 and now - start_time <= timeout:
            logger.trace(f'Buffered {len(self.data_buffer)} B.')
            self.data_lock.acquire()
            if len(self.data_buffer) == 0:
                self.data_lock.release()
                if not self.data_event.wait(RX_BYTE_TIMEOUT):
                    logger.debug('Timed out waiting for byte to be added to buffer.')
                    now = time.monotonic()
                    continue
                self.data_lock.acquire()

            # This timestamping would be more accurate if the time was capture in the data_received callback.
            self.last_rx_data_posix_timestamp_sec = time.time()

            logger.trace(f'Read got data. [size={len(self.data_buffer)} B]')
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
            now = time.monotonic()
            if return_any:
                break

        if self.rx_log:
            # Log timestamping could be improved by using write_with_timestamp with timestamps generated on reception.
            # Probably easier to do the logging in the reception thread when using a LogManager.
            self.rx_log.write(data)
        return data

    def start_read_thread(self):
        """!
        @brief This function must be called before any calls to @ref self.read.
        """
        self.rx_thread = serial.threaded.ReaderThread(self.serial_in, self)
        self.rx_thread.start()

    def stop(self):
        self.flush_rx()
        if self.rx_thread:
            self.rx_thread.stop()
            self.rx_thread = None
        self.serial_in.close()
        self.serial_out.close()

    # For serial.threaded.ReaderThread call.
    def __call__(self):
        return self

    # The callback used in the self.rx_thread when serial data is received.
    def data_received(self, data):
        logger.trace(f'RX thread got data. [size={len(data)} B]')
        if len(data) == 0:
            return

        self.data_lock.acquire()
        self.data_buffer += data
        if len(data) > MAX_DATA_BUFFER_SIZE:
            logger.error(
                'Serial RX buffer full. Dropping oldest data. [buffer_size=%d B, dropping=%d B]', MAX_DATA_BUFFER_SIZE,
                DATA_BUFFER_DROP_SIZE)
            self.data_buffer = self.data_buffer[DATA_BUFFER_DROP_SIZE:]
        self.data_event.set()
        self.data_lock.release()

    def flush_rx(self):
        self.data_lock.acquire()
        in_waiting = len(self.data_buffer)
        self.data_lock.release()
        logger.debug('Flushing data in buffer. [size=%d B]' % in_waiting)
        self.read(in_waiting)
