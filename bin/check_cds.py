#!/usr/bin/env python3

import os
import sys
from typing import  List, Optional

from fusion_engine_client.messages import (DataType, DataVersion, RawIMUOutput,
                                           PlatformStorageDataMessage,
                                           VersionInfoMessage)
from fusion_engine_client.parsers import MixedLogReader
from fusion_engine_client.analysis.data_loader import DataLoader
from fusion_engine_client.utils.log import DEFAULT_LOG_BASE_DIR, locate_log
import numpy as np
import numpy.typing as npt

# Add the parent directory to the search path to enable p1_runner package imports when not installed in Python.
repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(repo_root)

from p1_runner import trace as logging
from p1_runner.argument_parser import ArgumentParser
from p1_runner.import_config_loader import (add_config_loader_args, UserConfigType,
                                            get_config_loader_class)
from p1_runner.trace import HighlightFormatter

logger = logging.getLogger('point_one.check_cds')


def main():
    if getattr(sys, 'frozen', False):
        execute_command = os.path.basename(sys.executable)
    else:
        execute_command = os.path.basename(sys.executable)
        if execute_command.startswith('python'):
            execute_command += ' ' + os.path.basename(__file__)

    parser = ArgumentParser(
        usage='%s [OPTIONS]... LOG' % execute_command,
        description="""\
Check a log's raw IMU data for possible c_ds values.""" % execute_command)

    parser.add_argument(
        '-v', '--verbose', action='count', default=0,
        help="Print verbose/trace debugging messages.")

    log_group = parser.add_argument_group('Input File/Log Control')
    log_group.add_argument(
        '--log-base-dir', metavar='DIR', default=DEFAULT_LOG_BASE_DIR,
        help="The base directory containing FusionEngine logs to be searched if a log pattern is specified.")
    log_group.add_argument(
        'log', metavar='LOG',
        help="The log to be read. May be one of:\n"
             "- The path to a .p1log file or a file containing FusionEngine messages and other content\n"
             "- The path to a FusionEngine log directory\n"
             "- A pattern matching a FusionEngine log directory under the specified base directory "
             "(see find_fusion_engine_log() and --log-base-dir)")
    add_config_loader_args(parser)

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
    HighlightFormatter.install(color=True, standoff_level=logging.WARNING)

    # Locate the input file and set the output directory.
    # When using internal FE repo, add `load_original=True`.
    try:
        input_path, output_dir, log_id = locate_log(input_path=options.log, log_base_dir=options.log_base_dir,
                                                    return_output_dir=True, return_log_id=True, load_original=True)
    except TypeError:
        input_path, output_dir, log_id = locate_log(input_path=options.log, log_base_dir=options.log_base_dir,
                                                    return_output_dir=True, return_log_id=True)
    if input_path is None:
        # locate_log() will log an error.
        sys.exit(1)

    if log_id is None:
        logger.info('Loading %s.' % input_path)
    else:
        logger.info('Loading %s (log ID: %s).' % (input_path, log_id))


    imu_reader = DataLoader(input_path)
    imu_data = imu_reader.read(message_types=[RawIMUOutput.MESSAGE_TYPE], return_numpy=True)[RawIMUOutput.MESSAGE_TYPE]
    if len(imu_data.p1_time) == 0:
        logger.error('No raw IMU data found in log.')
        sys.exit(1)

    # Find any UserConfig c_ds present in the log.
    # Represented as 3x3 numpy matrix.
    c_ds_in_user_config: Optional[npt.ArrayLike] = None
    reader = MixedLogReader(input_path, message_types=(VersionInfoMessage,))
    try:
        # Determine the software version used to generate the log, and then download and import the matching version of
        # the user config extraction code from the published release.
        reader.clear_filters()
        reader.filter_in_place(key=VersionInfoMessage)
        reader.rewind()
        _, version_info = reader.read_next()
        UserConfig = get_config_loader_class(options, version_info.engine_version_str)

        reader.clear_filters()
        reader.filter_in_place(key=PlatformStorageDataMessage)
        reader.rewind()
        for _, message in reader:
            if message.data_type != DataType.USER_CONFIG:
                continue

            # Note: UserConfig is imported dynamically by import_config_loader().
            obj = UserConfig.deserialize(message.data)
            # NOTE: Assuming the first IMU is used.
            # The matrix contents are stored in row-major order.
            c_ds_array = np.array(obj.sensors.imus[0].c_ds.values)
            c_ds_in_user_config = c_ds_array.reshape((3,3))
    except StopIteration:
        logger.exception('No version information found in log.')
    except Exception as e:
        logger.exception('Failed to load UserConfig.')

    if c_ds_in_user_config is None:
        logger.error('Unable to load existing UserConfig.')
    else:
        print('c_ds loaded from log UserConfig is:')
        print(c_ds_in_user_config)

    p1_time = imu_data.p1_time
    accel_mps2 = imu_data.accel_mps2
    gyro_rps = imu_data.gyro_rps
    # TODO: Compute gravity vector and suggest possible c_ds

if __name__ == "__main__":
    main()
