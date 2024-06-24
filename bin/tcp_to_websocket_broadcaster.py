#!/usr/bin/env python3

import asyncio
import os
import sys

import websockets

# Add the parent directory to the search path to enable p1_runner package imports when not installed in Python.
repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(repo_root)

from p1_runner import trace as logging
from p1_runner.argument_parser import ArgumentParser, ExtendedBooleanAction


logger = logging.getLogger('point_one.tcp_to_websocket_broadcaster')
CLIENTS = set()


# Called when websocket client connects.
async def handler(websocket):
    CLIENTS.add(websocket)
    try:
        await websocket.wait_closed()
    finally:
        CLIENTS.remove(websocket)


async def tcp_data_broadcaster(host, port, send_as_string=False):
    while True:
        try:
            logger.info(f"Connecting to TCP server {host}:{port}.")
            reader, _ = await asyncio.open_connection(host, port)
            while True:
                data = await reader.read(1024)
                if not reader.at_eof():
                    if send_as_string:
                        data = data.decode('utf-8', errors="replace")
                    logger.trace(f'Sending {len(data)} B to {len(CLIENTS)} clients.')
                    websockets.broadcast(CLIENTS, data)
                else:
                    logger.info("TCP server disconnected.")
                    break
        except ConnectionRefusedError:
            logger.info("TCP connection refused.")
            await asyncio.sleep(5)


async def main():
    if getattr(sys, 'frozen', False):
        execute_command = os.path.basename(sys.executable)
    else:
        execute_command = os.path.basename(sys.executable)
        if execute_command.startswith('python'):
            execute_command += ' ' + os.path.basename(__file__)

    parser = ArgumentParser(
        description="""\
Forward the output from a TCP server to one or more websocket clients.
For example, this can let multiple websocket clients listen to the NMEA output
from a receiver's TCP server.""")

    parser.add_argument('device_tcp_address', metavar="ADDRESS:PORT",
                        help="The address to use when communicating with the device over TCP. The address must be "
                             "specified with a port like 'address:port'.")
    parser.add_argument('--websocket-port', type=int, default=30002,
                        help="The port for the websocket server to listen on.")
    parser.add_argument('--send-as-string', action=ExtendedBooleanAction,
                        help="Send the data to the websocket as a string instead of bytes. This is only used if the "
                             "websocket client can't handle binary data and all the data coming from the TCP server is "
                             "utf-8 encoded (ex. only sending NMEA).")
    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help="Print verbose/trace debugging messages. May be specified multiple times to increase "
                             "verbosity.")

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

    parts = options.device_tcp_address.split(':')
    if (len(parts) != 2 or not parts[1].isdigit()):
        logger.error(f'Invalid device TCP address "{options.device_tcp_address}". Must be "host:port".')
        sys.exit(1)
    host = parts[0]
    port = int(parts[1])

    # Start the websocket server
    async with websockets.serve(handler, host="", port=options.websocket_port):
        # Run forever handling the TCP connections and sending received data to websocket clients.
        await tcp_data_broadcaster(host, port, options.send_as_string)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
