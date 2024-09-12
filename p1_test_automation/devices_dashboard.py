#!/usr/bin/env python3

import os
import sys
import time
from typing import Any, Dict, List, NamedTuple, Optional, cast

import remi.gui as gui
from remi import App, start

# isort: split
from fusion_engine_client.messages import (CalibrationStage, CalibrationStatus,
                                           PoseMessage, SolutionType)
from fusion_engine_client.parsers.decoder import (FusionEngineDecoder,
                                                  MessageTuple)

# Add the parent directory to the search path to enable p1_runner package imports when not installed in Python.
repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(repo_root)
# Put imports that rely on this in their own indent block to avoid linter reordering.

# isort: split
from p1_runner import trace as logging
from p1_runner.argument_parser import ArgumentParser
from p1_test_automation.devices_config import (DataSource, DeviceConfig,
                                               load_config_set,
                                               open_data_source)

logger = logging.getLogger('point_one.test_automation.device_dashboard')


class State:
    """!
    Create a state class for values that will be directly mutated.
    """

    def __init__(self, data_source):
        self.first_seq_num = -1
        self.last_seq_num = -1
        self.seq_num_gaps = 0
        self.last_pose_host_time = -1.0
        self.last_pose_p1_time = -1.0
        self.largest_pose_p1_gap = 0.0
        self.disconnects = 0
        self.last_connect_attempt_host_time = time.time()
        self.data_source = data_source


class DeviceFields(NamedTuple):
    """!
    State for each device being tracked by the GUI.

    NamedTuple don't allow direct mutation, but allow the mutating values
    contained inside its members.
    """

    device: DeviceConfig
    decoder: FusionEngineDecoder
    status_lbl: gui.Label
    age_lbl: gui.TextInput
    cal_lbl: gui.TextInput
    state: State


COLOR_BAD = '#ff6666'
COLOR_OK = '#00ffFF'
COLOR_WARNING = '#dbd63d'
COLOR_GOOD = '#7FFFD4'

POSE_TIMEOUT = 5

CONNECTION_RETRY = 5


class MyApp(App):
    def idle(self):
        '''!
        Called periodically to determine the updates for the display.
        '''
        cur_time = time.time()
        for device_fields in self.devices_fields:
            if device_fields.state.data_source is not None:
                # Read data from the device and decode any FE messages.
                data = b''
                try:
                    data = device_fields.state.data_source.read(1024 * 1024 * 100, 0)
                except:
                    pass
                for hdr, msg in cast(List[MessageTuple], device_fields.decoder.on_data(data)):
                    # Update sequence number statistics.
                    if device_fields.state.first_seq_num < 0:
                        device_fields.state.first_seq_num = hdr.sequence_number
                    elif device_fields.state.last_seq_num + 1 != hdr.sequence_number:
                        device_fields.state.seq_num_gaps += 1
                    device_fields.state.last_seq_num = hdr.sequence_number

                    if isinstance(msg, PoseMessage):
                        # Update fix status and pose message timing.
                        device_fields.status_lbl.set_text("Fix: " + msg.solution_type.name)
                        if msg.solution_type == SolutionType.Invalid:
                            device_fields.status_lbl.style['background-color'] = COLOR_BAD
                        elif msg.solution_type == SolutionType.RTKFixed:
                            device_fields.status_lbl.style['background-color'] = COLOR_GOOD
                        else:
                            device_fields.status_lbl.style['background-color'] = COLOR_OK
                        device_fields.state.last_pose_host_time = cur_time
                        if device_fields.state.last_pose_p1_time > 0:
                            pose_gap = msg.p1_time.seconds - device_fields.state.last_pose_p1_time
                            device_fields.state.largest_pose_p1_gap = max(
                                pose_gap, device_fields.state.largest_pose_p1_gap
                            )
                        device_fields.state.last_pose_p1_time = msg.p1_time.seconds
                    elif isinstance(msg, CalibrationStatus):
                        # Update calibration status.
                        cal_str = f'''\
    Stage: {msg.calibration_stage.name}
    Gyro: {msg.gyro_bias_percent_complete}%, Accel: {msg.accel_bias_percent_complete}%, Mount: {msg.mounting_angle_percent_complete}%'''
                        device_fields.cal_lbl.set_text(cal_str)
                        if msg.calibration_stage == CalibrationStage.DONE:
                            device_fields.cal_lbl.style['background-color'] = COLOR_GOOD
                        else:
                            device_fields.cal_lbl.style['background-color'] = COLOR_OK

                if device_fields.state.last_pose_host_time > 0:
                    # Display connection statistics.
                    age = cur_time - device_fields.state.last_pose_host_time
                    num_msgs = device_fields.state.last_seq_num - device_fields.state.first_seq_num
                    device_fields.age_lbl.set_text(
                        f'''\
Age: {age:.2f} s, Disconnects: {device_fields.state.disconnects} , Msgs: {num_msgs}
Dropped: {device_fields.state.seq_num_gaps}, Max Pose Gap: {device_fields.state.largest_pose_p1_gap:.4f}'''
                    )
                    if device_fields.state.disconnects > 0:
                        device_fields.age_lbl.style['background-color'] = COLOR_WARNING
                    else:
                        device_fields.age_lbl.style['background-color'] = COLOR_GOOD
                else:
                    age = cur_time - device_fields.state.last_connect_attempt_host_time

                if age > POSE_TIMEOUT:
                    # Check if it's been too long since the last pose was received.
                    device_fields.state.data_source.stop()
                    device_fields.age_lbl.set_text(f'Disconnected')
                    device_fields.age_lbl.style['background-color'] = COLOR_BAD
                    device_fields.state.data_source = None
                    if device_fields.state.last_pose_host_time > 0:
                        device_fields.state.disconnects += 1
            else:
                # Periodically try to reconnect if connection is down.
                age = cur_time - device_fields.state.last_connect_attempt_host_time
                device_fields.age_lbl.set_text(f'Disconnected')
                device_fields.age_lbl.style['background-color'] = COLOR_BAD
                if age > CONNECTION_RETRY:
                    device_fields.state.last_connect_attempt_host_time = cur_time
                    device_fields.state.data_source = open_data_source(device_fields.device)
                    device_fields.state.last_pose_host_time = -1
                    device_fields.state.first_seq_num = -1
                    device_fields.state.last_seq_num = -1
                    device_fields.state.last_pose_p1_time = -1
                    device_fields.state.seq_num_gaps = 0
                    device_fields.state.largest_pose_p1_gap = 0
                    device_fields.age_lbl.set_text("Age: No data")

    def main(self, device_configs: List[DeviceConfig], data_sources: List[Optional[DataSource]]):
        '''!
        Called to initialize display elements.

        Parameters are passed from `start` call `userdata`.
        '''
        wid = gui.VBox(width='100%', height='100%')

        self.devices_fields: List[DeviceFields] = []
        for device_config, data_source in zip(device_configs, data_sources):
            device_box = gui.HBox(width='100%')
            name_lbl = gui.Label(device_config.name, width='10%')
            status_lbl = gui.Label("Fix: No data", width='20%', height='100%')
            status_lbl.style['background-color'] = COLOR_BAD
            age_lbl = gui.TextInput(False, "Age: No data", width='20%', height='100%')
            age_lbl.style['background-color'] = COLOR_BAD
            cal_lbl = gui.TextInput(False, "Cal: No data\n\n\n", width='30%', height='100%')
            cal_lbl.style['background-color'] = COLOR_BAD
            device_box.append(name_lbl)
            device_box.append(age_lbl)
            device_box.append(status_lbl)
            device_box.append(cal_lbl)
            wid.append(device_box)
            self.devices_fields.append(
                DeviceFields(device_config, FusionEngineDecoder(), status_lbl, age_lbl, cal_lbl, State(data_source))
            )

        # returning the root widget
        return wid

    def on_close(self):
        # Can be used to kill a background thread.
        super(MyApp, self).on_close()


def main():
    if getattr(sys, 'frozen', False):
        execute_command = os.path.basename(sys.executable)
    else:
        execute_command = os.path.basename(sys.executable)
        if execute_command.startswith('python'):
            execute_command += ' ' + os.path.basename(__file__)

    parser = ArgumentParser(
        usage='%s COMMAND [OPTIONS]...' % execute_command,
        description='Run a web server to display basic dashboard for devices.',
    )
    parser.add_argument(
        '-v',
        '--verbose',
        action='count',
        default=0,
        help="Print verbose/trace debugging messages. May be specified multiple times to increase " "verbosity.",
    )
    parser.add_argument(
        '-d',
        '--device-configurations',
        default=None,
        help="A JSON file with the configuration for the devices to display.",
    )
    args = parser.parse_args()

    if args.verbose == 0:
        logger.setLevel(logging.INFO)
        logging.basicConfig(level=logging.INFO, format='[%(filename)s:%(lineno)d] %(message)s', stream=sys.stdout)
    else:
        logger.setLevel(logging.DEBUG)
        logging.basicConfig(
            level=logging.INFO,
            format='[%(filename)s:%(lineno)-4d] %(asctime)s - %(levelname)-8s - %(message)s',
            stream=sys.stdout,
        )

    if args.verbose < 2:
        pass
    elif args.verbose == 2:
        logging.getLogger('point_one.fusion_engine.parsers.decoder').setLevel(logging.DEBUG)
        logging.getLogger('point_one.device_interface').setLevel(logging.DEBUG)
    else:
        logging.getLogger('point_one.fusion_engine.parsers.decoder').setLevel(logging.TRACE)
        logging.getLogger('point_one.device_interface').setLevel(logging.TRACE)

    config = load_config_set(args.device_configurations)

    data_sources: List[Optional[DataSource]] = [open_data_source(device_config) for device_config in config.devices]

    start(MyApp, debug=True, address='0.0.0.0', port=9000, update_interval=0.5, userdata=(config.devices, data_sources))


if __name__ == "__main__":
    main()
