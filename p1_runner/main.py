import argparse
import os
import signal
import sys
import threading

from fusion_engine_client.utils.log import DEFAULT_LOG_BASE_DIR
from fusion_engine_client.utils.socket_timestamping import TIMESTAMP_FILE_ENDING
from urllib3.util import parse_url

from . import trace as logging
from .argument_parser import ArgumentParser, ExtendedBooleanAction
from .runner import P1Runner


def main():
    # Parse command line arguments.
    if getattr(sys, 'frozen', False):
        execute_command = os.path.basename(sys.executable)
    else:
        execute_command = os.path.basename(sys.executable)
        if execute_command.startswith('python'):
            execute_command += ' runner.py'

    parser = ArgumentParser(usage='%s [OPTIONS]...' % execute_command,
                            description="""\
Control and log data from a Point One Quectel LG69T device.
    """,
                            epilog="""\
RTK CORRECTIONS

This tool can be configured to connect to an NTRIP server providing
RTCM-formatted GNSS RTK corrections using the --ntrip command-line argument.
Incoming corrections will then be forwarded to the device for navigation
purposes. When connecting to Point One's Polaris corrections service, the
--polaris argument may be used as a short-hand instead of the --ntrip argument.

Note: In order to use Point One's Polaris service, you must first obtain a
Polaris NTRIP password from Point One. When using Polaris, the --device-id used
by your device must be unique across _all_ devices using your assigned
password.

DATA LOGGING

This tool also generates Point One data logs, containing navigation solution
data and other detailed information. Generated logs may be sent to Point
One for post-processing and diagnostic purposes.

Note: When generating Point One data logs, you are strongly encouraged to
specify the --device-id argument and assign the device a unique name. This ID
is used by Point One to identify your device when looking at a data log.

TCP/WEBSOCKET OUTPUT

Lastly, this tool can be configured to relay incoming sensor data and NMEA
solution messages to users over TCP or websockets.

EXAMPLES

Log data with the device ID "my-device":

  %(command)s --device-id my-device

Connect to the Point One Polaris corrections service with an assigned password:

  %(command)s --device-id my-device --polaris PASSWORD

Connect to an NTRIP corrections service:

  %(command)s \
      --ntrip example-service.com:2101,MOUNTPOINT,USERNAME,PASSWORD

Forward NMEA output from the receiver to an application on TCP port 1234:

  %(command)s --tcp 1234
""" % {'command': execute_command})

    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help="Print verbose/trace debugging messages. May be specified multiple times to increase "
                             "verbosity.")

    device_group = parser.add_argument_group('Device')

    device_group.add_argument(
        '--device-id', default=P1Runner.DEFAULT_DEVICE_ID,
        help="An ID string used to identify this device in the recorded log data. Set to the value of the P1_DEVICE_ID "
             "environment variable if set. Defaults to '%s' if not specified." %
             P1Runner.DEFAULT_DEVICE_ID)
    device_group.add_argument(
        '--device-type', default=None, help=argparse.SUPPRESS)

    device_group.add_argument(
        '--device-port', '--port', default="auto",
        help="The serial port on which sensor data and solution output is being sent from the device to the host "
             "computer. If 'auto', the serial port will be located automatically by searching for a connected device.")

    device_group.add_argument(
        '--device-baud', '--baud', type=int, default=460800,
        help="The baud rate used by the device serial port (--device-port).")

    device_group.add_argument(
        '--corrections-port', default=None,
        help="The serial port to which RTK corrections data will be sent. By default corrections will be sent over the "
             "same serial port as '--device-port'.")

    device_group.add_argument(
        '--corrections-baud', type=int, default=460800,
        help="The baud rate used by the corrections serial port (--corrections-port).")

    device_group.add_argument(
        '--reset-type', choices=('hot', 'warm', 'pvt', 'diag', 'cold', 'none'), default='none',
        help="The type of reset to perform after connecting to the device. Resetting helps to facilitate deterministic "
             "log playback. When capturing a log for diagnostic and support purposes, use 'diag'.")
    device_group.add_argument(
        '--wait-for-reset', action=ExtendedBooleanAction, default=True,
        help="Wait for the reset to complete before logging data.")

    corr_group = parser.add_argument_group('Corrections')

    corr_sel_group = corr_group.add_mutually_exclusive_group()

    corr_sel_group.add_argument(
        '--ntrip', metavar='HOSTNAME[:PORT],MOUNTPOINT[,USERNAME[,PASSWORD]]',
        help="The NTRIP server hostname/port and credentials to use when receiving RTK corrections. If omitted, PORT "
             "defaults to 2101. Ignored if --polaris is specified.")
    corr_group.add_argument(
        '--ntrip-tls', action='store_true',
        help="Use TLS when connecting to the NTRIP service (not enabled by default; ignored if --ntrip is not "
             "specified).")
    corr_group.add_argument(
        '--ntrip-version', type=int,
        help="The NTRIP version to use for this connection: 1 (default) or 2.")
    corr_group.add_argument(
        '--ntrip-position', metavar='LATITUDE,LONGITUDE[,ALTITUDE]',
        help="A comma-separated string specifying a geodetic position (in degrees/meters) to be sent to the NTRIP "
             "server instead of the position reported by the receiver.")

    corr_group.add_argument(
        '--aux-ntrip', metavar='HOSTNAME[:PORT],MOUNTPOINT[,USERNAME[,PASSWORD]]',
        help="The hostname/port and credentials for an auxiliary NTRIP server to use as a source of ephemeris data. If "
             "omitted, PORT defaults to 2101. Set to 'polaris' to use the Polaris ephemeris stream.")
    corr_group.add_argument(
        '--aux-ntrip-tls', action='store_true',
        help="Use TLS when connecting to the NTRIP service (not enabled by default; ignored if --aux-ntrip is not "
             "specified).")
    corr_group.add_argument(
        '--aux-ntrip-version', type=int,
        help="The NTRIP version to use for this connection: 1 (default) or 2.")

    corr_sel_group.add_argument(
        '--polaris', metavar='[USERNAME,]PASSWORD',
        help="The username and password to use when receiving RTK corrections from Point One the Polaris network. "
             "--ntrip and --ntrip-tls will be ignored if --polaris is specified. If username is omitted, it will be "
             "set to the device ID (--device-id).")
    corr_group.add_argument(
        '--polaris-hostname', metavar='HOSTNAME', default='polaris.pointonenav.com',
        help="The hostname used to access the Point One Polaris network.")
    corr_group.add_argument(
        '--polaris-mountpoint', metavar='NAME', default='POLARIS',
        help="The NTRIP moutnpoint to access on the Point One Polaris network.")

    corr_group.add_argument(
        '--polaris-tls', action='store_true', default=True,
        help="Use TLS when connecting to the Polaris NTRIP service (default).")
    corr_group.add_argument(
        '--no-polaris-tls', action='store_false', dest='polaris_tls',
        help="Do not use TLS (i.e., use an unsecure connection) when connecting to the Polaris NTRIP service.")
    corr_group.add_argument(
        '--polaris-port', metavar='PORT', default=None, type=int,
        help="Use the specified port when connecting to Polaris. By default, the port will be selected automatically "
             "based on the --polaris-tls setting.")

    logging_group = parser.add_argument_group('Logging/Output')

    logging_group.add_argument(
        '--logs-base-dir', default=DEFAULT_LOG_BASE_DIR,
        help="The root directory in which log data will be stored.")
    logging_group.add_argument(
        '--log-format', metavar="FORMAT", choices=('nmea', 'none', 'p1log', 'raw'),
        default='raw',
        help="The format to use for the recorded log file.\n"
             "- nmea - A .nmea file is an ASCII file containing any NMEA-0183 messages output "
             "by the device.\n"
             "- none - Disable logging.\n"
             "- p1log - A .p1log file is a binary file containing Point One FusionEngine "
             "messages with solution data and other information output by the device.\n"
             "- raw - A .raw is a binary file containing all data received from the device UART. Typically this "
             "includes interleaved RTCM, FusionEngine, and NMEA messages. .raw files can be used for analysis and for "
             "post-processing and diagnostics. Use of .raw format is encouraged.\n"
             "\n"
             "Note that .p1log and .nmea formats do not contain measurement data, and cannot be used for diagnostics. "
             "When contacting Point One support, you must provide a log in .raw format.")

    logging_group.add_argument(
        '--rtt', metavar="MODE", dest="rtt_mode", choices=('none', 'log', 'print', 'all'),
        default='none',
        help="If connected to the device via JTAG, use the SEGGER JLink RTT Client tool to capture console output.\n"
             "- none - Disable console capture.\n"
             "- log - Capture output in a console.txt file within the recorded log directory.\n"
             "- print - Display output on the console.\n"
             "- all - Capture a console.txt file (log) and display output (print).\n"
             "- raw - A .raw is a binary file containing all data received from the device UART. Typically this "
             "includes interleaved RTCM, FusionEngine, and NMEA messages. .raw files can be used for analysis and for "
             "post-processing and diagnostics. Use of .raw format is encouraged.\n"
             "\n"
             "Note that .p1log and .nmea formats do not contain measurement data, and cannot be used for diagnostics. "
             "When contacting Point One support, you must provide a log in .raw format.")
    logging_group.add_argument(
        '--rtt-port', metavar="PORT", type=int,
        help="Specify the telnet port to use when establishing a JLink RTT connection.")
    logging_group.add_argument(
        '--rtt-kill-gdbserver', action=ExtendedBooleanAction,
        help='If true and JLinkGDBServer is running, close it when establishing a JLink RTT connection.')

    logging_group.add_argument(
        '--output-type', metavar="MODE", choices=('all', 'fusion_engine', 'nmea', 'legacy_nmea'),
        default='fusion_engine',
        help="The type of output to send to connected TCP/websocket clients:\n"
             "- all - Interleaved FusionEngine, RTCM, and NMEA messages\n"
             "- fusion_engine - Point One FusionEngine messages\n"
             "- nmea - NMEA-0183 messages"
             "- legacy_nmea - NMEA-0183 messages preceded by a legacy websocket header\n")
    logging_group.add_argument(
        '--tcp', metavar="[ADDRESS:]PORT",
        help="Listen for TCP connections on the specified address and port, and forward all incoming sensor data and "
             "NMEA output from the device to all clients.")
    logging_group.add_argument(
        '--websocket', '--ws', metavar="[ADDRESS:]PORT",
        help="Listen for websocket connections on the specified address and port, and forward all incoming sensor data "
             "and NMEA output from the device to all clients.")

    logging_group.add_argument(
        '--log-created-cmd', metavar="CMD",
        help="Execute the specified command after the log is created.")

    logging_group.add_argument(
        '--log-timestamps', action='store_true',
        help=f'Generate an "input.raw{TIMESTAMP_FILE_ENDING}" file with a mapping of the run time to a byte offsets in '
              'the data log.')

    ref_group = parser.add_argument_group('Reference FusionEngine Device')
    ref_group.add_argument(
        '--reference', metavar="HOSTNAME[:PORT]",
        help="Connect to the specified TCP hostname and port and listen for incoming FusionEngine Pose messages to be "
             "recorded in a 'reference.*' file in the log directory. If omitted, the port defaults to 30201.")
    logging_group.add_argument(
        '--reference-format', metavar="FORMAT", choices=('csv', 'p1log'),
        default='p1log',
        help="The format to use when recording a reference file.")

    external_serial_recorder = parser.add_argument_group('External Serial Recorder')
    external_serial_recorder.add_argument(
        '--external-port', default=None,
        help="If set, connect to an additional serial device on the specified port and capture its data in a file "
             "specified by --external-output-path and/or send it corrections if --external-corrections is "
             "specified."
    )

    external_serial_recorder.add_argument(
        '--external-baud', type=int, default=460800,
        help="The baud rate used by the external device serial port (--external-port)."
    )

    external_serial_recorder.add_argument(
        '--external-output-path', default=None,
        help="The relative path within the log directory to the output file where data received from the external "
             "serial device will be stored."
    )

    external_serial_recorder.add_argument(
        '--external-corrections', action=ExtendedBooleanAction,
        help='If true, send corrections to the external serial device.'
    )

    display_group = parser.add_argument_group('Display')
    display_group.add_argument(
        '--wheel-tick-display', type=str, choices=['gui', 'text'], nargs='?', const='gui',
        help="Print wheel tick debugging information. Options are:\n"
             "- gui - Print a user-friendly display on the console (default)\n"
             "- text - Print a simpler text display for console with limited functionality")

    options = parser.parse_args()

    # Configure console logging.
    if options.verbose == 0:
        logging.basicConfig(level=logging.INFO, format='%(message)s', stream=sys.stdout)
    else:
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                            stream=sys.stdout)

        if options.verbose == 1:
            logging.getLogger('point_one.p1_runner').setLevel(logging.DEBUG)
            logging.getLogger('point_one.ntrip_client').setLevel(logging.DEBUG)
            logging.getLogger('point_one.rtt_client').setLevel(logging.DEBUG)
            logging.getLogger('ntripstreams').setLevel(logging.DEBUG)
            logging.getLogger('ntripstreams.receive').setLevel(logging.INFO)
        elif options.verbose == 2:
            logging.getLogger('point_one.p1_runner').setLevel(logging.TRACE)
            logging.getLogger('point_one.p1_runner.output').setLevel(logging.DEBUG)
            logging.getLogger('point_one.p1_runner.websocket').setLevel(logging.DEBUG)
            logging.getLogger('point_one.ntrip_client').setLevel(logging.TRACE)
            logging.getLogger('point_one.rtt_client').setLevel(logging.DEBUG)
            logging.getLogger('ntripstreams').setLevel(logging.DEBUG)
            logging.getLogger('ntripstreams.receive').setLevel(logging.INFO)
        elif options.verbose == 3:
            logging.Logger.root.setLevel(logging.DEBUG)
            logging.getLogger('point_one.p1_runner').setLevel(logging.TRACE - 1)
            logging.getLogger('point_one.p1_runner.output').setLevel(logging.TRACE)
            logging.getLogger('point_one.p1_runner.websocket').setLevel(logging.TRACE)
            logging.getLogger('point_one.ntrip_client').setLevel(logging.TRACE)
            logging.getLogger('point_one.rtt_client').setLevel(logging.DEBUG)
            logging.getLogger('ntripstreams').setLevel(logging.DEBUG)
            logging.getLogger('ntripstreams.receive').setLevel(logging.DEBUG)
            logging.getLogger('point_one.nmea_framer').setLevel(logging.DEBUG)
            logging.getLogger('point_one.rtcm_framer').setLevel(logging.DEBUG)
            logging.getLogger('point_one.fusion_engine').setLevel(logging.DEBUG)
        else:
            logging.Logger.root.setLevel(logging.DEBUG)
            logging.getLogger('point_one.p1_runner').setLevel(logging.TRACE - 1)
            logging.getLogger('point_one.p1_runner.output').setLevel(logging.TRACE)
            logging.getLogger('point_one.p1_runner.websocket').setLevel(logging.TRACE)
            logging.getLogger('point_one.output_server').setLevel(logging.TRACE)
            logging.getLogger('point_one.ntrip_client').setLevel(logging.TRACE)
            logging.getLogger('point_one.rtt_client').setLevel(logging.DEBUG)
            logging.getLogger('ntripstreams').setLevel(logging.DEBUG)
            logging.getLogger('ntripstreams.receive').setLevel(logging.DEBUG)
            logging.getLogger('point_one.nmea_framer').setLevel(logging.TRACE)
            logging.getLogger('point_one.rtcm_framer').setLevel(logging.TRACE)
            logging.getLogger('point_one.fusion_engine').setLevel(logging.TRACE)

    logging.getLogger('websockets.server').setLevel(logging.WARNING)

    # Configure TCP/websocket output.
    if options.tcp is not None:
        parts = options.tcp.split(':')
        if len(parts) == 2:
            output_tcp_address = (parts[0], int(parts[1]))
        else:
            output_tcp_address = ('', int(parts[0]))
    else:
        output_tcp_address = None

    if options.websocket is not None:
        parts = options.websocket.split(':')
        if len(parts) == 2:
            output_websocket_address = (parts[0], int(parts[1]))
        else:
            output_websocket_address = ('', int(parts[0]))
    else:
        output_websocket_address = None

    # Configure reference input.
    if options.reference is not None:
        parts = options.reference.split(':')
        if len(parts) == 2:
            reference_tcp_address = (parts[0], int(parts[1]))
        elif len(parts) == 1:
            reference_tcp_address = (parts[0], 30201)
        else:
            raise ValueError('Invalid reference address specifier.')
    else:
        reference_tcp_address = None

    # Configure the runner.
    if options.device_id is None:
        device_id = os.environ.get('P1_DEVICE_ID', P1Runner.DEFAULT_DEVICE_ID)
    else:
        device_id = options.device_id

    logger = logging.getLogger('point_one.p1_runner.__main__')

    runner = P1Runner(device_id=device_id, device_type=options.device_type,
                      reset_type=options.reset_type, wait_for_reset=options.wait_for_reset,
                      device_port=options.device_port, device_baudrate=options.device_baud,
                      corrections_port=options.corrections_port, corrections_baudrate=options.corrections_baud,
                      external_port=options.external_port, external_baudrate=options.external_baud,
                      external_output_path=options.external_output_path,
                      external_corrections=options.external_corrections,
                      logs_base_dir=options.logs_base_dir, log_format=options.log_format,
                      log_created_cmd=options.log_created_cmd, log_timestamps=options.log_timestamps,
                      output_tcp_address=output_tcp_address, output_websocket_address=output_websocket_address,
                      output_type=options.output_type,
                      reference_tcp_address=reference_tcp_address, reference_format=options.reference_format,
                      rtt_mode=options.rtt_mode, rtt_port=options.rtt_port,
                      rtt_kill_gdbserver=options.rtt_kill_gdbserver,
                      wheel_tick_display_mode=options.wheel_tick_display)

    # Configure GNSS corrections (Polaris over NTRIP, or custom NTRIP server).
    if options.ntrip_position is not None:
        parts = [p.strip() for p in options.ntrip_position.split(",")]
        if len(parts) < 2 or len(parts) > 3:
            logger.error('Invalid NTRIP position specifier.')
            sys.exit(1)

        lla_deg = [None, None, 0.0]
        try:
            lla_deg[0] = float(parts[0])
            lla_deg[1] = float(parts[1])
            if len(parts) == 3:
                lla_deg[2] = float(parts[2])
            runner.set_ntrip_position_override(lla_deg)
        except ValueError:
            logger.error('Invalid NTRIP position specifier.')
            sys.exit(1)

    if options.polaris is not None:
        if options.polaris_tls:
            port = 2102 if options.polaris_port is None else options.polaris_port
            url = f'https://{options.polaris_hostname}:{port}'
        else:
            port = 2101 if options.polaris_port is None else options.polaris_port
            url = f'http://{options.polaris_hostname}:{port}'

        parts = options.polaris.split(",")
        if len(parts) == 1:
            username = options.device_id
            password = parts[0]
        elif len(parts) != 2:
            logger.error('You must specify a valid username and password for the Polaris service.')
            sys.exit(1)
        else:
            username, password = parts

        # Default to NTRIP v2 for Polaris connections.
        ntrip_version = options.ntrip_version if options.ntrip_version is not None else 2

        runner.connect_to_ntrip(url=url, mountpoint=options.polaris_mountpoint, username=username, password=password,
                                version=ntrip_version)
    elif options.ntrip is not None:
        parts = options.ntrip.split(",")
        if len(parts) < 2:
            logger.error('You must specify a valid URL and mountpoint for NTRIP connections.')
            sys.exit(1)

        url = parts[0]
        mountpoint = parts[1]
        if len(parts) > 2:
            username = parts[2]
        else:
            username = None
        if len(parts) > 3:
            password = parts[3]
        else:
            password = None

        # Default to NTRIP v1 for normal NTRIP connections.
        ntrip_version = options.ntrip_version if options.ntrip_version is not None else 1

        if parse_url(url).scheme is None:
            scheme = 'https' if options.ntrip_tls else 'http'
            url = '%s://%s' % (scheme, url)
        runner.connect_to_ntrip(url=url, mountpoint=mountpoint, username=username, password=password,
                                version=ntrip_version)

    # Configure an auxiliary NTRIP ephemeris source.
    if options.aux_ntrip is not None:
        if options.aux_ntrip == 'polaris':
            url = 'http://polaris.pointonenav.com:2101'
            mountpoint = 'EPHEM'
            username = None
            password = None
            ntrip_version = 1
        else:
            parts = options.aux_ntrip.split(",")
            if len(parts) < 2:
                logger.error('You must specify a valid URL and mountpoint for NTRIP connections.')
                sys.exit(1)

            url = parts[0]
            mountpoint = parts[1]
            if len(parts) > 2:
                username = parts[2]
            else:
                username = None
            if len(parts) > 3:
                password = parts[3]
            else:
                password = None

            # Default to NTRIP v1 for normal NTRIP connections.
            ntrip_version = options.aux_ntrip_version if options.aux_ntrip_version is not None else 1

            if parse_url(url).scheme is None:
                scheme = 'https' if options.aux_ntrip_tls else 'http'
                url = '%s://%s' % (scheme, url)

        runner.connect_to_ntrip(url=url, mountpoint=mountpoint, username=username, password=password,
                                version=ntrip_version, stream='aux')

    # Start the runner.
    logger.debug('Starting runner...')
    runner.start()

    # Wait for SIGINT (Ctrl-C) or SIGTERM and then shut down.
    shutdown = threading.Event()

    def signal_handler(signum, frame):
        logger.debug('Received signal %s (%d).' % (signal.Signals(signum).name, signum))
        shutdown.set()
        signal.signal(signum, signal.SIG_DFL)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    if os.name == 'nt':
        # Normally, the signal handler gets called as soon as the user sends a SIGINT (hits Ctrl-C) and notifies the
        # shutdown event to wake up its wait() call. In Python 3 in Windows though, the signal handler will only get
        # called _after_ wait() returns, whether or not it's a blocking call. To get around this, we simply use a short
        # timeout to allow the signal handler to get called and set the flag to shutdown gracefully.
        while not shutdown.wait(0.1):
            pass
    else:
        shutdown.wait()

    logger.debug('Shutting down application...')
    runner.stop()
    runner.join()
