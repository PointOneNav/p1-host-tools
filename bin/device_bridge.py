#!/usr/bin/env python3

import os
import re
import signal
import socket
import sys
import threading
from datetime import datetime

import serial
import serial.threaded

# Add the parent directory to the search path to enable p1_runner package imports when not installed in Python.
repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(repo_root)

from p1_runner import trace as logging
from p1_runner.argument_parser import ArgumentParser

logger = logging.getLogger('point_one.bridge_devices')


class DataHandler:
    def __init__(self, source, destination):
        self.source = source
        self.destination = destination
        self.bytes_sent = 0
        self.last_print_bytes = 0

    def data_received(self, data):
        bytes_sent = self.bytes_sent + len(data)

        if logger.isEnabledFor(logging.DEBUG):
            if logger.isEnabledFor(logging.getTraceLevel()):
                logger.trace('Sending %d B from %s -> %s. [%d B total]' %
                             (len(data), self.source.name, self.destination.name, self.bytes_sent))
            elif (self.bytes_sent - self.last_print_bytes) > 10 * 1024:
                logger.debug('Sent %d B total from %s -> %s.' %
                             (self.bytes_sent, self.source.name, self.destination.name))
                self.last_print_bytes = self.bytes_sent

        if self.destination.write(data):
            self.bytes_sent = bytes_sent


class SerialDataHandler(DataHandler, serial.threaded.Protocol):
    def __init__(self, *args, **kwargs):
        DataHandler.__init__(self, *args, **kwargs)

    def __call__(self):
        return self


class SerialConnection:
    def __init__(self, port, baudrate=460800):
        self.serial = serial.Serial(port=port, baudrate=baudrate)
        self.name = self.serial.name
        self.handler = None
        self.read_thread = None

    def start(self, other_device):
        if other_device is not None:
            self.handler = SerialDataHandler(source=self, destination=other_device)
            self.read_thread = serial.threaded.ReaderThread(self.serial, self.handler)
            self.read_thread.start()

    def stop(self):
        if self.read_thread is not None:
            self.read_thread.stop()

    def get_bytes_sent(self):
        if self.handler is None:
            return 0
        else:
            return self.handler.bytes_sent

    def write(self, data):
        self.serial.write(data)
        return True


class TCPServer(threading.Thread):
    def __init__(self, port):
        super().__init__()
        self.name = f'tcp://:{port}'
        self.port = port
        self.handler = None
        self.socket = None
        self.client_mutex = threading.Lock()
        self.client_socket = None
        self.shutdown_event = threading.Event()

    def start(self, other_device):
        if other_device is not None:
            self.handler = DataHandler(source=self, destination=other_device)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.socket.bind(('', self.port))
        self.socket.listen(1)
        self.socket.settimeout(0.5)
        super().start()

    def stop(self):
        if self.socket is not None:
            self.shutdown_event.set()
            self.socket.close()
            self.join()

    def get_bytes_sent(self):
        if self.handler is None:
            return 0
        else:
            return self.handler.bytes_sent

    def write(self, data):
        with self.client_mutex:
            if self.client_socket is not None:
                try:
                    self.client_socket.send(data)
                    return True
                except (BrokenPipeError, ConnectionResetError):
                    return False
        return False

    def run(self):
        client_name = None
        print_listening = True

        def _on_disconnect():
            nonlocal client_name, print_listening
            logger.debug(f'Client {client_name} disconnected.')
            with self.client_mutex:
                self.client_socket = None
            client_name = None
            print_listening = True

        while not self.shutdown_event.is_set():
            try:
                if self.client_socket is None:
                    if print_listening:
                        logger.debug(f'Listening for incoming connections on {self.name}.')
                        print_listening = False

                    client_socket, client_addr = self.socket.accept()
                    client_name = f'tcp://{client_addr[0]}:{client_addr[1]}'
                    logger.debug(f'Connected to {client_name}.')
                    with self.client_mutex:
                        self.client_socket = client_socket

                logger.trace(f'Listening for data from {client_name}.')
                data = self.client_socket.recv(1024)
                if len(data) == 0:
                    _on_disconnect()
                elif self.handler is not None:
                    self.handler.data_received(data)
            except socket.timeout:
                pass
            except (BrokenPipeError, ConnectionResetError):
                _on_disconnect()
            except OSError as e:
                break


class TCPClient(threading.Thread):
    def __init__(self, hostname, port):
        super().__init__()
        self.name = f'tcp://{hostname}:{port}'
        self.hostname = hostname
        self.port = port
        self.handler = None
        self.socket = None
        self.connetion_mutex = threading.Lock()
        self.is_connected = False
        self.shutdown_event = threading.Event()

    def start(self, other_device):
        if other_device is not None:
            self.handler = DataHandler(source=self, destination=other_device)
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.socket.settimeout(0.5)
        super().start()

    def stop(self):
        if self.socket is not None:
            self.shutdown_event.set()
            self.socket.close()
            self.join()

    def get_bytes_sent(self):
        if self.handler is None:
            return 0
        else:
            return self.handler.bytes_sent

    def write(self, data):
        with self.connetion_mutex:
            if self.is_connected:
                self.socket.send(data)
                return True
        return False

    def run(self):
        print_connecting = True

        def _on_disconnect():
            nonlocal print_connecting
            logger.debug(f'Connection {self.name} disconnected remotely.')
            with self.connetion_mutex:
                self.is_connected = False
            print_connecting = True

        retry_timeout_sec = 0.0
        while not self.shutdown_event.wait(retry_timeout_sec):
            try:
                if not self.is_connected:
                    if print_connecting:
                        logger.debug(f'Connecting to {self.name}.')
                        print_connecting = False

                    addr = socket.gethostbyname(self.hostname)
                    self.socket.connect((addr, self.port))
                    logger.debug(f'Connected to {self.hostname if addr == self.hostname else addr}.')
                    with self.connetion_mutex:
                        self.is_connected = True

                data = self.socket.recv(1024)
                if len(data) == 0:
                    _on_disconnect()
                elif self.handler is not None:
                    self.handler.data_received(data)
            except socket.timeout:
                pass
            except (ConnectionRefusedError, ConnectionAbortedError):
                if self.is_connected:
                    _on_disconnect()
                retry_timeout_sec = 1.0
            except (BrokenPipeError, ConnectionResetError):
                _on_disconnect()
            except OSError as e:
                break


def _open_connection(descriptor, default_baud=460800):
    SERIAL_CONNECTION = re.compile(r'^(?:tty://)?([/\-_.a-zA-Z0-9]+)(?::([0-9]+))?$')
    TCP_CONNECTION = re.compile(r'^tcp://([\-_.a-zA-Z0-9]+)?(?::([0-9]+))$')

    m = SERIAL_CONNECTION.match(descriptor)
    if m is not None:
        baudrate = int(m.group(2)) if m.group(2) is not None else default_baud
        return SerialConnection(port=m.group(1), baudrate=baudrate)

    m = TCP_CONNECTION.match(descriptor)
    if m is not None:
        if m.group(1) is None:
            return TCPServer(port=int(m.group(2)))
        else:
            return TCPClient(hostname=m.group(1), port=int(m.group(2)))

    raise ValueError(f"Invalid device descriptor '{descriptor}'.")


def main():
    if getattr(sys, 'frozen', False):
        execute_command = os.path.basename(sys.executable)
    else:
        execute_command = os.path.basename(sys.executable)
        if execute_command.startswith('python'):
            execute_command += ' ' + os.path.basename(__file__)

    parser = ArgumentParser(
        usage='%s [OPTIONS]... DEVICE_A DEVICE_B' % execute_command,
        description="""\
Bridge two connected devices, forwarding the output from each device to the
input of the other device. This is most common if:
- One device is acting as an RTK base station for another device
- One device is a heading sensor (i.e., heading secondary device), sending
  heading measurements to a navigation engine (i.e., heading primary device)

The devices may be connected using one of the following methods:
- Serial: [tty://]/path/to/device[:BAUD] (e.g., /dev/ttyUSB0:115200)
  - The default baud rate is 460800
- Listen for TCP connections: tcp://:PORT (e.g., tcp://:12345)
- Connect to a TCP server: tcp://HOSTNAME:PORT (e.g., tcp://remote-machine:12345
  or tcp://192.168.0.3:12345)
""",
        epilog="""\
EXAMPLE USAGE (SERIAL <-> SERIAL)

Send data in both directions between /dev/ttyUSB0 and /dev/ttyUSB5, for
example, communicating GNSS and heading measurements between a heading
primary device on ttyUSB0 and secondary device on ttyUSB5:
    %(command)s /dev/ttyUSB0 /dev/ttyUSB5

Send data from /dev/ttyUSB0 to /dev/ttyUSB5, for example, sending RTK
corrections from a base station on ttyUSB0 to a rover on ttyUSB5:
    %(command)s --direction=a-b /dev/ttyUSB0 /dev/ttyUSB5


EXAMPLE USAGE (SERIAL <-> TCP SERVER)

Host a TCP server on port 12345, transferring data between connected TCP
clients and serial device /dev/ttyUSB0:
    %(command)s /dev/ttyUSB0 tcp://:12345
""" % {'command': execute_command})

    parser.add_argument('-d', '--direction', choices=('both', 'a-b', 'b-a'), default='both',
                        help="The direction in which data should be transferred:\n"
                             "  both - Send data in both directions\n"
                             "  a-b - Send data from device A to device B\n"
                             "  b-a - Send data from device B to device A\n")
    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help="Print verbose/trace debugging messages. May be specified multiple times to increase "
                             "verbosity.")

    parser.add_argument('device_a', help="The descriptor for the connection to device A.")
    parser.add_argument('device_b', help="The descriptor for the connection to device B.")

    options = parser.parse_args()

    if options.verbose == 0:
        logging.basicConfig(level=logging.INFO, format='%(message)s', stream=sys.stdout)
    else:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                            stream=sys.stdout)
        if options.verbose == 1:
            logging.getLogger('point_one').setLevel(logging.DEBUG)
        elif options.verbose > 1:
            logging.getLogger('point_one').setLevel(logging.getTraceLevel(depth=options.verbose - 1))

    try:
        if options.direction == 'a-b':
            direction_str = '->'
        elif options.direction == 'b-a':
            direction_str = '<-'
        else:
            direction_str = '<->'

        device_a = _open_connection(options.device_a)
        device_b = _open_connection(options.device_b)

        start_time = datetime.now()

        def _print_status():
            logger.info('[elapsed: %d sec, sent: %d B -> %s (A), %d bytes -> %s (B)]' %
                        ((datetime.now() - start_time).total_seconds(),
                         device_b.get_bytes_sent(), device_a.name,
                         device_a.get_bytes_sent(), device_b.name))

        logger.info('Connecting device A (%s) %s device B (%s).' %
                    (options.device_a, direction_str, options.device_b))

        logger.debug('Starting device A thread.')
        if options.direction == 'both' or options.direction == 'a-b':
            device_a.start(other_device=device_b)
        else:
            device_a.start(other_device=None)

        logger.debug('Starting device B thread.')
        if options.direction == 'both' or options.direction == 'b-a':
            device_b.start(other_device=device_a)
        else:
            device_b.start(other_device=None)

        shutdown_pending = threading.Event()

        def _handle_signal(sig, frame):
            shutdown_pending.set()
            signal.signal(sig, signal.SIG_DFL)

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)
        while not shutdown_pending.wait(5.0):
            _print_status()

        logger.info('Shutting down.')

        logger.debug('Stopping device A thread.')
        device_a.stop()
        logger.debug('Stopping device B thread.')
        device_b.stop()

        _print_status()
    except Exception as e:
        logger.error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
