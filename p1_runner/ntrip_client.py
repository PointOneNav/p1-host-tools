import asyncio
from datetime import datetime, timezone
from functools import reduce
import math
import operator
import ssl
import threading
import time
import traceback
from urllib3.util import parse_url

import ntripstreams

from . import trace as logging


class NTRIPClient(threading.Thread):
    logger = logging.getLogger('point_one.ntrip_client')
    rx_logger = logger.getChild('rx')

    def __init__(self, url=None, mountpoint=None, username=None, password=None, version=2, data_callback=None):
        super().__init__(name='ntrip_%s' % repr(mountpoint))

        self.ntrip = None
        self.connected = False
        self.startup_gga_message = None

        self.url = None
        self.mountpoint = None
        self.username = None
        self.password = None
        self.ntrip_version = None
        self.set_server(url, mountpoint, username, password, version)

        self.data_callback = data_callback

        self.event_loop = asyncio.new_event_loop()

    def set_server(self, url, mountpoint, username=None, password=None, version=2):
        if parse_url(url).scheme is None:
            self.url = 'http://%s' % url
        else:
            self.url = url

        self.mountpoint = mountpoint
        self.username = username
        self.password = password
        self.ntrip_version = version

    def set_data_callback(self, callback):
        self.data_callback = callback

    def is_connected(self):
        return self.connected

    def send_position(self, lla_deg: list, time: datetime = None):
        if not self.connected:
            self.logger.trace('Not connected. Ignoring position update. [%.8f, %.8f, %.2f]' % tuple(lla_deg))
            return False

        utc_now = datetime.now(timezone.utc)
        if time is None:
            time = utc_now
        else:
            # If the user didn't specify a timezone, assume it's the local clock's timezone.
            if time.tzinfo is None:
                local_timezone = utc_now.astimezone().tzinfo
                time.replace(tzinfo=local_timezone)

            # Now adjust to UTC.
            time = time.astimezone(timezone.utc)

        # Construct a NMEA GPGGA sentence.
        #
        # $GPGGA,134658.00,5106.9792,N,11402.3003,W,2,09,1.0,1048.47,M,-16.27,M,08,AAAA*60
        #
        # Note:
        # - We report the ellipsoid height instead of orthometric height (MSL), and set the geoid undulation to 0,
        #   assuming that actual MSL height won't matter for base station association
        # - We assume solution type 1 (standalone GNSS)
        # - We don't populate any of the satellite-related stuff
        lat_str = self._nmea_deg_to_ddmm(lla_deg[0], False)
        lon_str = self._nmea_deg_to_ddmm(lla_deg[1], True)
        message = '$GPGGA,%s,%s,%s,1,,,%.2f,M,0.0,M,,' % (time.strftime('%H%M%S.%f'), lat_str, lon_str, lla_deg[2])
        message = self._append_nmea_checksum(message)

        self.logger.debug('Sending position update. [position=[%.8f, %.8f, %.2f]]' %
                          (lla_deg[0], lla_deg[1], lla_deg[2]))

        return self.send_nmea(message)

    def send_nmea(self, message):
        if not self.connected:
            self.logger.trace('Not connected. Ignoring NMEA message. [%s]' % repr(message))
            return False

        if not message.startswith('$'):
            message = '$' + message
        if not message.endswith('\r\n'):
            message = message + '\r\n'
        if message[3:7] == 'GGA,':
            # Cache this message to send if ntrip client needs to reconnect.
            self.startup_gga_message = message
        self.logger.debug('Sending NMEA message. [%s]' % repr(message))
        return self._send_async(message)

    def start(self):
        self.logger.debug('Starting receive thread for mountpoint %s.' % self.mountpoint)
        super().start()

    def stop(self):
        if self.is_alive():
            self.logger.debug('Stopping receive thread for mountpoint %s.' % self.mountpoint)
            for task in asyncio.all_tasks(loop=self.event_loop):
                task.cancel()
            self.logger.debug('Closing NTRIP connection.')
            if self.ntrip is not None:
                result = asyncio.run_coroutine_threadsafe(self.ntrip.closeNtripConnection(), loop=self.event_loop)
                try:
                    result.result()
                except TimeoutError:
                    # This can throw a timeout on a socket recv() on shutdown.
                    pass
                except ssl.SSLError:
                    # This can throw an APPLICATION_DATA_AFTER_CLOSE_NOTIFY exception on shutdown.
                    pass
                except Exception as e:
                    self.logger.warning('Caught unexpceted exception while closing NTRIP connection: %s' % repr(e))
                    self.logger.debug(traceback.format_exc())
                self.ntrip = None
            self.event_loop.call_soon_threadsafe(self.event_loop.stop)

    async def __stop(self):
        self.event_loop.stop()

    def run(self):
        asyncio.run_coroutine_threadsafe(self.__connect(), loop=self.event_loop)
        try:
            self.event_loop.run_forever()
        finally:
            self.event_loop.close()

    async def __connect(self):
        # Connect to the NTRIP server.
        self.logger.debug('Connecting to server. [url=%s, ntrip_version=%d, mountpoint=%s, username=%s]' %
                          (self.url, self.ntrip_version, self.mountpoint, repr(self.username)))
        while not self.connected:
            try:
                self.ntrip = ntripstreams.NtripStream()
                await self.ntrip.requestNtripStream(casterUrl=self.url, mountPoint=self.mountpoint, user=self.username,
                                                    passwd=self.password, ntripVersion=self.ntrip_version)
                self.connected = True
                self.logger.debug('Connected successfully. Scheduling data reception.')
                if self.startup_gga_message:
                    self.logger.debug('Sending cached GGA message.')
                    self.send_nmea(self.startup_gga_message)
                asyncio.run_coroutine_threadsafe(self.__receive_data(), loop=self.event_loop)
            except ConnectionError as e:
                self.logger.error('Unexpected error connecting to NTRIP server: %s' % repr(e))
                self.logger.debug(traceback.format_exc())
                self.logger.error('Retrying in 5 seconds.')
                await asyncio.sleep(5.0, loop=self.event_loop)
                asyncio.run_coroutine_threadsafe(self.__connect(), loop=self.event_loop)

    async def __receive_data(self):
        try:
            self.rx_logger.trace('Waiting for data.', depth=2)
            data = await asyncio.wait_for(self.ntrip.getRawData(1024), timeout=0.5)
            self.rx_logger.trace('Received %d bytes from mountpoint %s.' % (len(data), self.mountpoint))
            if self.data_callback is not None:
                self.data_callback(data)
            asyncio.run_coroutine_threadsafe(self.__receive_data(), loop=self.event_loop)
        except asyncio.CancelledError as e:
            raise e
        except asyncio.TimeoutError:
            self.rx_logger.trace('Read timed out with no data. Reading again.', depth=2)
            asyncio.run_coroutine_threadsafe(self.__receive_data(), loop=self.event_loop)
        except Exception as e:
            self.logger.error('Unexpected error waiting for data: %s' % repr(e))
            self.logger.debug(traceback.format_exc())
            self.logger.error('Reconnecting to server.')
            self.connected = False
            # While it seems like this should be called, it hangs indefinitely
            # On ValueError from garbage data in, and ConnectionAbortedError.
            # Skipping it has no impact on reconnection in those cases.
            # await self.ntrip.closeNtripConnection()
            self.ntrip = None
            asyncio.run_coroutine_threadsafe(self.__connect(), loop=self.event_loop)

    def _send_async(self, data):
        if not isinstance(data, bytes):
            data = data.encode('ISO-8859-1')

        async def _send(data):
            if self.connected:
                # Note: Despite the name, this function sends arbitrary data and does not require RTCM.
                await self.ntrip.sendRtcmFrame(data)

        asyncio.run_coroutine_threadsafe(_send(data), loop=self.event_loop)
        return True

    @classmethod
    def _nmea_deg_to_ddmm(cls, angle_deg, is_longitude=False):
        if is_longitude:
            direction = 'E' if angle_deg >= 0.0 else 'W'
        else:
            direction = 'N' if angle_deg >= 0.0 else 'S'

        abs_angle_deg = math.fabs(angle_deg)
        degree = math.floor(abs_angle_deg)
        minute = (abs_angle_deg - degree) * 60.0

        return '%d%011.8f,%s' % (degree, minute, direction)

    @classmethod
    def _append_nmea_checksum(cls, nmea_str, append_newline=True):
        result = '%s*%02X' % (nmea_str, cls._calculate_nmea_checksum(nmea_str))
        if append_newline:
            result += '\r\n'
        return result

    @classmethod
    def _calculate_nmea_checksum(cls, nmea_str):
        if nmea_str[0] == '$':
            nmea_str = nmea_str[1:]
        return reduce(operator.xor, map(ord, nmea_str), 0)
