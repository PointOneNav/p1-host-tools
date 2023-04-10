import colorama
import numpy as np
import serial

from fusion_engine_client.messages import *

from p1_runner.device_interface import RESPONSE_TIMEOUT, DeviceInterface
from p1_runner import trace as logging

MPS_TO_KPH = 3.6
MPS_TO_MPH = 2.23694


class WheelTickDisplay:
    logger = logging.getLogger('point_one.p1_runner.wheel_tick_display')

    def __init__(self, device_serial: serial.Serial, display_mode: str = 'gui'):
        self.display_mode = display_mode

        self.device_interface = DeviceInterface(device_serial, device_serial, RESPONSE_TIMEOUT)
        self.hardware_tick_config = None
        self.wheel_config = None

        self.p1_time = None
        self.tick_count = None
        self.speed_mps = None
        self.gear = GearType.UNKNOWN
        self.nav_engine_speed_mps = None

        self.last_data_p1_time = None
        self.last_data_warning_p1_time = None

        self.printed_once = False

    def query_wheel_config(self):
        # First, issue a set request for the wheel config. When the response comes back, we'll send a request for the
        # hardware tick config. Once we have both, we'll actually print the result.
        self.device_interface.get_config(ConfigurationSource.ACTIVE, WheelConfig.GetType())

    def handle_message(self, header: MessageHeader, response_payload: MessagePayload, *args):
        if header.message_type == MessageType.RAW_VEHICLE_TICK_OUTPUT:
            self._handle_vehicle_tick_message(response_payload)
        elif header.message_type == MessageType.VEHICLE_SPEED_OUTPUT:
            self._handle_vehicle_speed_message(response_payload)
        elif header.message_type == MessageType.POSE:
            self._handle_pose_message(response_payload)
        elif header.message_type == MessageType.CONFIG_RESPONSE:
            self._handle_config_response(response_payload)

    def _handle_config_response(self, message: ConfigResponseMessage):
        if message.config_type == ConfigType.WHEEL_CONFIG and self.wheel_config is None:
            self.wheel_config = message.config_object

            # Now that we have the wheel config, query the tick config. Once we have the result, we'll print the full
            # configuration.
            self.device_interface.get_config(ConfigurationSource.ACTIVE, HardwareTickConfig.GetType())
        elif message.config_type == ConfigType.HARDWARE_TICK_CONFIG and self.hardware_tick_config is None:
            self.hardware_tick_config = message.config_object

            # We should now have wheel and tick configuration. Print the settings.
            self.logger.info("############################ CONFIGURATION SETTINGS ############################")
            self.logger.info(self.hardware_tick_config)
            self.logger.info(self.wheel_config)
            self.logger.info("############################## END CONFIGURATION ###############################")

    def _handle_vehicle_tick_message(self, message: RawVehicleTickOutput):
        # If we never got a speed message corresponding with the previous tick message, print the old tick data now.
        # Otherwise, we'll print when the speed message comes in.
        if self.tick_count is not None:
            self._print_state()

        self.p1_time = message.get_p1_time()
        self.tick_count = message.tick_count
        self.gear = message.gear

        self.last_data_p1_time = self.p1_time

    def _handle_vehicle_speed_message(self, message: VehicleSpeedOutput):
        p1_time = message.get_p1_time()

        # Check if we have pending tick data that corresponds with this speed message.
        if self.p1_time is not None:
            # If it is from an earlier time, we may have missed its speed message and should print it now.
            if self.p1_time < p1_time:
                self._print_state()
            # If it is from a later time, that is unexpected: the device should always output ticks before speed.
            elif self.p1_time > p1_time:
                self.logger.warning('Out-of-order tick data detected. Discarding ticks @ [%s].' % self.p1_time)
                self.tick_count = None
            # If the two times match, we have consistent data.
            else:
                pass
        # If we do not have tick data to go with this speed data, print now. We assume ticks should always come before
        # speed.
        else:
            pass

        self.p1_time = p1_time
        self.speed_mps = message.vehicle_speed_mps
        self.gear = message.gear
        self._print_state()

        self.last_data_p1_time = self.p1_time

    def _handle_pose_message(self, message: PoseMessage):
        # Note that the pose output will almost certainly not align in time with the tick/speed measurements. We just
        # display the latest pose speed estimate and do not attempt to align it.
        if message.solution_type == SolutionType.Invalid:
            self.nav_engine_speed_mps = None
        else:
            self.nav_engine_speed_mps = message.velocity_body_mps[0]

            p1_time = message.get_p1_time()
            if self.last_data_warning_p1_time is None:
                self.last_data_warning_p1_time = p1_time

            if self.last_data_p1_time is None:
                self.last_data_p1_time = p1_time

            elapsed_sec = float(p1_time - self.last_data_p1_time)
            if elapsed_sec > 1.0 and float(p1_time - self.last_data_warning_p1_time) >= 5.0:
                self.logger.warning('No measurement data seen in %d seconds.' % elapsed_sec)
                self.last_data_warning_p1_time = p1_time

                self.p1_time = p1_time
                self._print_state()

    def _print_state(self):
        if self.display_mode == 'gui':
            # Clear the previous text on each update.
            if self.printed_once:
                num_lines = 4
                command = colorama.ansi.clear_line()
                for i in range(num_lines - 1):
                    command += colorama.Cursor.UP() + colorama.ansi.clear_line()
                command += '\r'
                print(command, end='', flush=True)
            else:
                self.printed_once = True

            # Now print the display:
            #   P1 Time:         123.456 sec
            #   Tick Count:    123456789 ticks  |  Gear: FORWARD
            #   Speed Measurement:  12.3 m/s (44.3 km/h = 27.5 mph)
            #   Nav Engine Speed:   12.2 m/s (43.9 km/h = 27.3 mph)
            tick_str = '% 12d' % self.tick_count if self.tick_count is not None else '% 12c' % '?'
            if self.speed_mps is None or np.isnan(self.speed_mps):
                speed_mps_str = '% 5c' % '?'
            else:
                speed_mps = round(self.speed_mps * 10.0) / 10.0
                speed_mps_str = '%5.1f m/s (%.1f km/h = %.1f mph)' % \
                                (speed_mps, speed_mps * MPS_TO_KPH, speed_mps * MPS_TO_MPH)
            if self.nav_engine_speed_mps is None or np.isnan(self.nav_engine_speed_mps):
                nav_speed_mps_str = '% 6c' % '?'
            else:
                nav_engine_speed_mps = round(self.nav_engine_speed_mps * 10.0) / 10.0
                nav_speed_mps_str = '%6.1f m/s (%.1f km/h = %.1f mph)' % \
                                    (nav_engine_speed_mps, nav_engine_speed_mps * MPS_TO_KPH,
                                     nav_engine_speed_mps * MPS_TO_MPH)

            print('P1 Time: %15.3f sec' % float(self.p1_time))
            print('Tick Count: %s ticks  |  Gear: %s' % (tick_str, self.gear))
            print('Speed Measurement: %s' % speed_mps_str)
            print('Nav Engine Speed: %s' % nav_speed_mps_str, end='', flush=True)
        else:
            tick_str = '%d' % self.tick_count if self.tick_count is not None else '?'
            speed_str = '%.1f' % (round(self.speed_mps * 10.0) / 10.0) if self.speed_mps is not None else '?'
            self.logger.info('%s | %s ticks --> %s m/s | Gear: %s' %
                             (str(self.p1_time), tick_str, speed_str, str(self.gear)))

        self.p1_time = None
        self.tick_count = None
        self.speed_mps = None
        self.gear = GearType.UNKNOWN
