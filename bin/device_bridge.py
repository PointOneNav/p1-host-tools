#!/usr/bin/env python3

from datetime import datetime
import os
import signal
import sys
import threading

import serial
import serial.threaded

# Add the parent directory to the search path to enable p1_runner package imports when not installed in Python.
repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(repo_root)

from p1_runner import trace as logging
from p1_runner.argument_parser import ArgumentParser

logger = logging.getLogger('point_one.bridge_devices')


class DataHandler(serial.threaded.Protocol):
    def __init__(self, source: serial.Serial, destination: serial.Serial):
        self.source = source
        self.destination = destination
        self.bytes_sent = 0
        self.last_print_bytes = 0

    def __call__(self):
        return self

    def data_received(self, data):
        self.bytes_sent += len(data)

        if logger.isEnabledFor(logging.DEBUG):
            if logger.isEnabledFor(logging.getTraceLevel()):
                logger.trace('Sending %d B from %s -> %s. [%d B total]' %
                             (len(data), self.source.name, self.destination.name, self.bytes_sent))
            elif (self.bytes_sent - self.last_print_bytes) > 10 * 1024:
                logger.debug('Sent %d B total from %s -> %s.' %
                             (self.bytes_sent, self.source.name, self.destination.name))
                self.last_print_bytes = self.bytes_sent

        self.destination.write(data)


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
Bridge two serial devices, forwarding the output from each device to the input
of the other device. This is most common if:
- One device is acting as an RTK base station for another device
- One device is a heading sensor (i.e., heading secondary device), sending
  heading measurements to a navigation engine (i.e., heading primary device)
""",
        epilog="""\
EXAMPLE USAGE

Send data in both directions between /dev/ttyUSB0 and /dev/ttyUSB5, for
example, communicating GNSS and heading measurements between a heading
primary device on ttyUSB0 and secondary device on ttyUSB5:
    %(command)s /dev/ttyUSB0 /dev/ttyUSB5

Send data from /dev/ttyUSB0 to /dev/ttyUSB5, for example, sending RTK
corrections from a base station on ttyUSB0 to a rover on ttyUSB5:
    %(command)s --direction=a-b /dev/ttyUSB0 /dev/ttyUSB5
""" % {'command': execute_command})

    parser.add_argument('--baud-a', type=int, default=460800,
                        help="The baud rate used by the DEVICE_A.")
    parser.add_argument('--baud-b', type=int, default=460800,
                        help="The baud rate used by the DEVICE_B.")
    parser.add_argument('-d', '--direction', choices=('both', 'a-b', 'b-a'), default='both',
                        help="The direction in which data should be transferred:\n"
                             "  both - Send data in both directions\n"
                             "  a-b - Send data from device A to device B\n"
                             "  b-a - Send data from device B to device A\n")
    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help="Print verbose/trace debugging messages. May be specified multiple times to increase "
                             "verbosity.")

    parser.add_argument('device_a', help="The device or COM name for the device A serial port.")
    parser.add_argument('device_b', help="The device or COM name for the device B serial port.")

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

        logger.info('Connecting device A (%s) %s device B (%s).' %
                    (options.device_a, direction_str, options.device_b))

        with serial.Serial(port=options.device_a, baudrate=options.baud_a) as device_a, \
             serial.Serial(port=options.device_b, baudrate=options.baud_b) as device_b:
            read_handler_a = DataHandler(source=device_a, destination=device_b)
            read_handler_b = DataHandler(source=device_b, destination=device_a)

            start_time = datetime.now()
            def _print_status():
                logger.info('[elapsed: %d sec, sent: %d B -> %s (A), %d bytes -> %s (B)]' %
                            ((datetime.now() - start_time).total_seconds(),
                             read_handler_b.bytes_sent, device_a.name,
                             read_handler_a.bytes_sent, device_b.name))

            if options.direction == 'both' or options.direction == 'a-b':
                logger.debug('Starting A->B thread.')
                read_thread_a = serial.threaded.ReaderThread(device_a, read_handler_a)
                read_thread_a.start()
            else:
                read_thread_a = None

            if options.direction == 'both' or options.direction == 'b-a':
                logger.debug('Starting B->A thread.')
                read_thread_b = serial.threaded.ReaderThread(device_b, read_handler_b)
                read_thread_b.start()
            else:
                read_thread_b = None

            shutdown_pending = threading.Event()

            def _handle_signal(sig, frame):
                shutdown_pending.set()
                signal.signal(sig, signal.SIG_DFL)

            signal.signal(signal.SIGINT, _handle_signal)
            signal.signal(signal.SIGTERM, _handle_signal)
            while not shutdown_pending.wait(5.0):
                _print_status()

            logger.info('Shutting down.')

            if read_thread_b is not None:
                logger.debug('Stopping B->A thread.')
                read_thread_b.stop()
            if read_thread_a is not None:
                logger.debug('Stopping A->B thread.')
                read_thread_a.stop()

            _print_status()
    except Exception as e:
        logger.error(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
