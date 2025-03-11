#!/usr/bin/env python3

import os
import sys
from collections import defaultdict
from textwrap import indent
from typing import Optional, TypeAlias

import numpy as np
import numpy.typing as npt
from fusion_engine_client.analysis.data_loader import DataLoader
from fusion_engine_client.messages import (DataType, Direction,
                                           PlatformStorageDataMessage,
                                           RawIMUOutput, VersionInfoMessage)
from fusion_engine_client.parsers import MixedLogReader
from fusion_engine_client.utils.log import DEFAULT_LOG_BASE_DIR, locate_log

# Add the parent directory to the search path to enable p1_runner package imports when not installed in Python.
repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(repo_root)

from p1_runner import trace as logging
from p1_runner.argument_parser import ArgumentParser
from p1_runner.import_config_loader import (UserConfigType,
                                            add_config_loader_args,
                                            get_config_loader_class)
from p1_runner.trace import HighlightFormatter

logger = logging.getLogger('point_one.check_cds')


MAX_SEARCH_TIME_SEC = 10 * 60


CDSValue: TypeAlias = tuple[int, int, int, int, int, int, int, int, int]


def matrix_to_key(c_ds: npt.NDArray) -> CDSValue:
    return tuple(int(x) for x in c_ds.reshape([c_ds.size]))  # type: ignore


_VEC_DIRECTION_MAPPING = {
    tuple([1, 0, 0]): Direction.FORWARD,
    tuple([-1, 0, 0]): Direction.BACKWARD,
    tuple([0, 1, 0]): Direction.LEFT,
    tuple([0, -1, 0]): Direction.RIGHT,
    tuple([0, 0, 1]): Direction.UP,
    tuple([0, 0, -1]): Direction.DOWN,
}


def vector_to_direction(vec: npt.NDArray) -> Direction:
    key = tuple(int(i) for i in vec)
    return _VEC_DIRECTION_MAPPING.get(key, Direction.INVALID)


def matrix_to_config(c_ds: npt.ArrayLike):
    c_ds_matrix = np.array(c_ds).reshape([3, 3])
    return {
        'x_direction': vector_to_direction(c_ds_matrix[0, :]),
        'z_direction': vector_to_direction(c_ds_matrix[2, :])
    }

# - Define "s" frame defined by X, Y, and Z axes, as the frame in which its
#   positive Z direction is approximately pointing up. In navigation, ths "s"
#   frame is very close to the vehicle body frame, "b", only with small
#   calibration angles are left uncomputed to reach "b".
# - Define raw IMU device frame, "d", defined by x, y, and z axes.
# - This method finds rotation matrix c_"sd" (from "d" to "s") first by finding
#   which axis of x, y, or z is most closely aligned to the vertical direction,
#   then returns c_"ds" (which is the transpose of c_sd) as needed for our c++
#   code.
#
# - Note: This method does _Not_ find which axis of x, y, or z is close to the
#   forward or lateral direction, but only finds the axis closest to the
#   vertical direction, as HITL test requires so.


def find_cds(time_sec: npt.NDArray, accel_mps2: npt.NDArray):
    c_ds_last: Optional[CDSValue] = None
    start_time = time_sec[0]

    counts: dict[tuple[int, int, int, int, int, int, int, int, int], int] = defaultdict(int)

    #  Step 0. c_sd is unknown at this point
    #  c_sd  x  y  z
    #  X     ?  ?  ?  x
    #  Y     ?  ?  ?  y
    #  Z     ?  ?  ?  z
    for time, accel in zip(time_sec, accel_mps2.transpose()):
        elapsed = time - start_time
        if elapsed > MAX_SEARCH_TIME_SEC:
            logger.info(f'Stopping search after {MAX_SEARCH_TIME_SEC}s limit.')
            break

        c_sd = np.zeros([3, 3])
        # Z
        #  Step 1. Find the 3rd row of c_sd by finding the max norm in accel x,
        #  y, or z. It should be close to +9.8 or -9.8 unless heavily tilted.
        #  c_sd  x  y  z
        #  X     ?  ?  ?  x
        #  Y     ?  ?  ?  y
        #  Z     ?  ?  ?  z <-----
        # For the test data as an example, accel_x shows -9.8, accel_y shows a
        # small value, accel_z shows a small value, too. Them max norm is found
        # as i = 0 (x), and its sign is -1. We put the found sign to the
        # c_sd[2, i]
        zind = np.argmax(np.abs(accel))
        z_sign = 0
        if accel[zind] >= 0:
            zsign = 1
        else:
            zsign = -1
        c_sd[2, zind] = zsign

        # X
        # Step 2. Fill the 1st row of c_sd. Note that 1st row and 2nd row are
        # arbitrary, whichever goes as long as they form right-handed coordinate
        # system. For the 1st row, we can put "1" for the column not selected
        # by the 3rd row.
        #  c_sd  x  y  z
        #  X     ?  ?  ?  x <-----
        #  Y     ?  ?  ?  y
        #  Z    -1  0  0  z
        if zind == 0:
            xind = 1
        elif zind == 1 or zind == 2:
            xind = 0
        c_sd[0, xind] = 1

        # Y
        # Step 3. Fill the 2nd row of c_sd. This can be done by vector product
        # of the Z(3rd row) and X(1st row).
        #  c_sd  x  y  z
        #  X     0  1  0  x
        #  Y     ?  ?  ?  y <-----
        #  Z    -1  0  0  z
        c_sd[1] = np.cross(c_sd[2, :], c_sd[0, :])

        # Summary
        # We've found c_sd as
        #  c_sd  x  y  z
        #  X     0  1  0  x
        #  Y     0  0 -1  y
        #  Z    -1  0  0  z

        # Navigation requires its transpose, c_ds
        c_ds = matrix_to_key(c_sd.transpose())
        # Check if computed c_ds is continuously the same for a certain numbers.
        if c_ds_last is not None:
            if c_ds != c_ds_last:
                logger.info(f"At time={elapsed:.2f}, a different c_ds was computed from gravity.")
        counts[c_ds] += 1
        c_ds_last = c_ds

    sorted_counts = dict(sorted(counts.items(), key=lambda item: item[1]))
    total = len(time_sec)
    for c_ds_key, count in sorted_counts.items():
        print(f'Orientation found {count}/{total} ({count/total*100.0:.1f}%) of log:')
        print('''\
    "sensors": {{
        "imus/0": {{
            "c_ds": {{
                "values": [
                    {},  {}, {},
                    {}, {}, {},
                    {},  {}, {}
                ]
            }}
        }}
    }}
'''.format(*[float(x) for x in c_ds_key]))
        c_ds_last = c_ds_key
    print(f'    {matrix_to_config(c_ds_key)}')
    return c_ds_last


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
Check a log's raw IMU data for possible c_ds values.""")

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
        logging.basicConfig(level=logging.INFO, format='%(message)s', stream=sys.stderr)
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
            c_ds_in_user_config = c_ds_array.reshape((3, 3))
    except StopIteration:
        logger.warning('No version information found in log.')
    except Exception as e:
        logger.exception('Failed to load UserConfig.')

    if c_ds_in_user_config is None:
        logger.warning('Unable to load existing UserConfig.')
    else:
        print('c_ds loaded from log UserConfig is:')
        print(c_ds_in_user_config)

    p1_time = imu_data.p1_time
    accel_mps2 = imu_data.accel_mps2
    gyro_rps = imu_data.gyro_rps
    computed_cds = find_cds(p1_time, accel_mps2)


if __name__ == "__main__":
    main()
