import asyncio
import errno
import os
import socket
import threading
from threading import Thread, Event
import traceback

import websockets

from . import trace as logging
from .eos_message import WebsocketHeader


class WebSocketServerThread(Thread):
    logger = logging.getLogger('point_one.p1_runner.websocket')

    def __init__(self, websocket_address, legacy_nmea=False):
        Thread.__init__(self)
        self.loop = None
        self.started = Event()
        self.queues = []
        self.exit = None
        self.websocket_address = websocket_address
        self.legacy_nmea = legacy_nmea

    async def _handle_ws_connection(self, connection):
        queue = asyncio.Queue()
        self.queues.append(queue)
        path = connection.path
        self.logger.debug('Websocket got connection from %s.' % repr(path))
        while True:
            data = await queue.get()
            if self.exit.done() or data is None:
                break
            else:
                if self.legacy_nmea:
                    data = WebsocketHeader().pack(return_buffer=True) + data.strip()

                try:
                    await connection.send(data)
                except:
                    break
        self.queues.remove(queue)
        self.logger.debug('Websocket done with %s.' % repr(path))

    async def _run_server(self):
        self.logger.debug('Websocket server running.')
        async with websockets.serve(self._handle_ws_connection, host=self.websocket_address[0],
                                    port=self.websocket_address[1]):
            self.logger.debug('Waiting for stop request.')
            await self.exit
        self.logger.debug('Websocket server exited.')

    def run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.exit = asyncio.Future()
        # Notify other threads that the loop is up and running.
        self.loop.call_soon(self.started.set)
        self.loop.run_until_complete(self._run_server())

    def stop(self):
        # Trigger _run_server to complete.
        self.logger.debug('Sending stop request to thread.')
        self.loop.call_soon_threadsafe(self.exit.set_result, 'exit')
        # Close out any active _handle_ws_connection calls.
        self.logger.debug('Sending close request to all connections.')
        self.send(None)

    async def _send(self, data):
        if data is not None and len(self.queues) > 0:
            self.logger.trace('Sending %d bytes to %d clients.' %
                              (len(data), len(self.queues)))
        for q in self.queues:
            q.put_nowait(data)

    def send(self, data):
        # Check to make sure the loop is up and running.
        self.started.wait()
        asyncio.run_coroutine_threadsafe(self._send(data), loop=self.loop)


class OutputServer(object):
    logger = logging.getLogger('point_one.p1_runner.output')

    def __init__(self, tcp_address=None, websocket_address=None, legacy_nmea=False):
        self.is_open = False

        self.tcp_address = tcp_address
        self.tcp_socket = None
        self.tcp_thread = None
        self.tcp_lock = threading.Lock()
        self.tcp_clients = {}

        self.ws_address = websocket_address
        self.ws_server = None
        self.legacy_nmea = legacy_nmea

    def start(self):
        if self.tcp_address is not None:
            self.logger.debug('Listening for incoming TCP connections on tcp://%s:%d.' %
                              (self.tcp_address[0], self.tcp_address[1]))
            self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_socket.settimeout(0.5)
            self.tcp_socket.bind(self.tcp_address)
            self.tcp_socket.listen()

            self.tcp_thread = threading.Thread(
                name='quectel_tcp', target=self._run_tcp)

        if self.ws_address is not None:
            self.logger.debug('Listening for incoming websocket connections on ws://%s:%d.' %
                              (self.ws_address[0], self.ws_address[1]))
            self.ws_server = WebSocketServerThread(self.ws_address, legacy_nmea=self.legacy_nmea)
            self.ws_server.start()

        if self.tcp_thread is not None:
            self.is_open = True
            self.tcp_thread.start()

    def stop(self):
        if self.is_open:
            self.is_open = False
            if self.tcp_socket is not None:
                self.logger.debug('Closing TCP socket.')
                self.tcp_socket.close()

        if self.ws_server is not None:
            self.logger.debug('Closing websocket server.')
            self.ws_server.stop()

    def join(self):
        if self.tcp_thread is not None:
            self.logger.debug('Joining TCP thread.')
            self.tcp_thread.join()
        if self.ws_server is not None:
            self.logger.debug('Joining websocket thread.')
            self.ws_server.join()
        self.logger.debug('Finished.')

    def send(self, data):
        if not isinstance(data, (bytes, bytearray)):
            data = data.encode('ISO-8859-1')

        if self.ws_server is not None:
            self.ws_server.send(data)

        with self.tcp_lock:
            if len(self.tcp_clients) > 0:
                self.logger.trace('Sending %d bytes to %d TCP clients.' % (
                    len(data), len(self.tcp_clients)))
                closed = []
                for addr, client in self.tcp_clients.items():
                    try:
                        client.sendall(data)
                    except Exception as e:
                        self.logger.debug(
                            'Client socket tcp://%s:%d closed. [%s]' % (addr[0], addr[1], repr(e)))
                        closed.append(addr)
                        continue

                if len(closed) > 0:
                    self.tcp_clients = {
                        a: c for a, c in self.tcp_clients.items() if a not in closed}

    def _run_tcp(self):
        while True:
            try:
                client, addr = self.tcp_socket.accept()
                self.logger.debug(
                    'New output connection from tcp://%s:%d.' % (addr[0], addr[1]))
                with self.tcp_lock:
                    self.tcp_clients[addr] = client
            except socket.timeout:
                if self.is_open:
                    continue
                else:
                    self.logger.debug('TCP listening socket closed.')
                    break
            except Exception as e:
                if isinstance(e, OSError) and (e.errno == errno.EBADF or
                                               (os.name == 'nt' and e.errno == errno.WSAENOTSOCK)):
                    self.logger.debug('TCP listening socket closed.')
                else:
                    self.logger.error(
                        'Unexpected error from TCP socket:\r%s' % traceback.format_exc())
                break

        self.logger.debug('TCP thread finished.')
