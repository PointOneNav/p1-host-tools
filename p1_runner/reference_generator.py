from enum import IntEnum
import socket
import threading

from fusion_engine_client.parsers import FusionEngineDecoder
from fusion_engine_client.messages import *

from . import trace as logging


class NovatelPosType(IntEnum):
    NoSolution = 0
    FixedPos = 1
    FixedHeight = 2
    DopplerVelocity = 8
    SinglePoint = 16
    PRDiff = 17
    WAAS = 18
    Propogated = 19
    Omnistar = 20
    L1Float = 32
    IonoFreeFloat = 33
    NarrowFloat = 34
    L1Integer = 48
    NarrowInteger = 50
    RTKDirectIns = 51
    INSSBAS = 52
    INSPR = 53
    INSPRDiff = 54
    INSRTKFloat = 55
    INSRTKFixed = 56
    OmnistarHP = 64
    OmnistarXP = 65
    PPPConverging = 68
    PPP = 69
    PPPBasicConverging = 77
    PPPBasic = 78

    @classmethod
    def from_solution_type(cls, solution_type: SolutionType):
        NovatelPosType
        if solution_type == SolutionType.Invalid:
            return NovatelPosType.NoSolution
        elif solution_type == SolutionType.AutonomousGPS:
            return NovatelPosType.INSPR
        elif solution_type == SolutionType.DGPS:
            return NovatelPosType.INSPRDiff
        elif solution_type == SolutionType.RTKFloat:
            return NovatelPosType.INSRTKFloat
        elif solution_type == SolutionType.RTKFixed:
            return NovatelPosType.INSRTKFixed
        elif solution_type == SolutionType.Integrate:
            return NovatelPosType.Propogated
        else:
            return NovatelPosType.INSPR


class ReferenceGenerator(threading.Thread):
    logger = logging.getLogger('point_one.p1_runner.reference_generator')

    def __init__(self, hostname, port, path, format='auto'):
        super().__init__(name='ref_%s' % hostname)

        self.address = (hostname, port)

        self.path = path
        self.file = None

        if format == 'auto':
            if self.path.endswith('.csv'):
                self.format = 'csv'
            if self.path.endswith('.p1log'):
                self.format = 'p1log'
            else:
                raise ValueError('Unrecognized reference file format.')
        else:
            self.format = format

        self.decoder = FusionEngineDecoder(max_payload_len_bytes=PoseMessage.calcsize(), warn_on_unrecognized=False,
                                           return_bytes=True)
        self.decoder.add_callback(PoseMessage.MESSAGE_TYPE, self._handle_pose_message)

        self.shutdown_pending = threading.Event()
        self.sock = None

        self.num_entries = 0

    def stop(self):
        self.shutdown_pending.set()

    def run(self):
        self.sock = None
        bytes_received = 0
        while not self.shutdown_pending.is_set():
            # Try to connect to the device.
            if self.sock is None:
                self.logger.debug('Connecting to tcp://%s:%d...' % self.address)
                try:
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.settimeout(1.0)
                    sock.connect(self.address)
                    self.logger.debug('Connected to tcp://%s:%d.' % self.address)
                    sock.settimeout(0.2)
                    self.sock = sock
                    bytes_received = 0
                except socket.timeout:
                    self.logger.debug('Connection request timed out.')
                    continue
                except ConnectionRefusedError:
                    self.logger.warning('Connection refused by tcp://%s:%d. Retrying in 5 seconds.' % self.address)
                    self.shutdown_pending.wait(5.0)
                    continue
                except ConnectionAbortedError:
                    self.logger.warning('Connection aborted (tcp://%s:%d unreachable). Retrying in 5 seconds.' %
                                        self.address)
                    self.shutdown_pending.wait(5.0)
                    continue
                except OSError as e:
                    self.logger.warning('%s (tcp://%s:%d unreachable). Retrying in 5 seconds.' %
                                        (str(e), self.address[0], self.address[1]))
                    self.shutdown_pending.wait(5.0)
                    continue

            # Read data.
            try:
                received_data = self.sock.recv(1024)
                if len(received_data) == 0:
                    self.logger.debug('Connection closed remotely.')
                    self.sock.close()
                    self.sock = None
                    continue

                bytes_received += len(received_data)
                self.logger.trace('Received %d bytes from device. [total_bytes_received=%d]' %
                                  (len(received_data), bytes_received),
                                  depth=2)

                self.decoder.on_data(received_data)
            except socket.timeout:
                continue
            except KeyboardInterrupt:
                break
            except OSError:
                # _handle_pose_message() couldn't open the output file. Bail.
                break

        # Close the socket.
        if self.sock is not None:
            self.logger.debug('Closing connection. [total_bytes_received=%d, total_entries=%d]' %
                              (bytes_received, self.num_entries))
            self.sock.close()
            self.sock = None

        # Close the output file.
        if self.file is not None:
            self.file.close()
            self.file = None

    def _handle_pose_message(self, header: MessageHeader, pose: PoseMessage, raw_bytes: bytes):
        # Ignore invalid solutions.
        if pose.solution_type == SolutionType.Invalid:
            return
        # reference.csv is timestamped in GPS time, so if we don't have that discard this solution.
        elif not pose.gps_time:
            return

        gps_time_sec = float(pose.gps_time)
        SEC_PER_WEEK = (7 * 24 * 3600.0)
        week = math.floor(gps_time_sec / SEC_PER_WEEK)
        tow = gps_time_sec - week * SEC_PER_WEEK

        self.num_entries += 1
        self.logger.trace('Received pose data. [gps_time=%d:%.3f, solution_type=%s (%d), total_entries=%d]' %
                          (week, tow, pose.solution_type.name, pose.solution_type.value, self.num_entries))

        # Create the file when the first message is received, that way we don't create an empty file if we never get any
        # data.
        if self.file is None:
            try:
                if self.format == 'csv':
                    self.file = open(self.path, 'wt')
                    self.file.write("gps_time, latitude_deg, longitude_deg, height_geoid_m, height_ellipsoid_m, "
                                    "pos_type, solution status, time status, lat_std_dev_m, lon_std_dev_m, "
                                    "height_std_dev_m\n")
                else:
                    self.file = open(self.path, 'wb')
            except OSError as e:
                self.logger.error("Unable to open reference file '%s'." % self.path)
                # Clear the path so we don't try to open this file again on every message.
                self.path = None
                raise e

        if self.format == 'csv':
            # For legacy reasons, reference.csv uses Novatel pos types.
            #
            # Note that we do not populate the deprecated solution or time status fields, or the LLA std devs.
            geoid_height_m = pose.lla_deg[2] - pose.undulation_m
            self.file.write("%.3f, %.8f, %.8f, %.3f, %.3f, %d, 0, 0, 0, 0, 0\n" %
                            (float(pose.gps_time), pose.lla_deg[0], pose.lla_deg[1], geoid_height_m, pose.lla_deg[2],
                             int(NovatelPosType.from_solution_type(pose.solution_type))))
        else:
            self.file.write(raw_bytes)
