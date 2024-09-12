#!/usr/bin/env python3

import importlib
import os
import re
import sys
import tempfile
import zipfile

import boto3
import botocore.exceptions
from fusion_engine_client.messages import (DataType, DataVersion,
                                           PlatformStorageDataMessage,
                                           Response, VersionInfoMessage)
from fusion_engine_client.parsers import MixedLogReader
from fusion_engine_client.utils.log import DEFAULT_LOG_BASE_DIR, locate_log

# Add the parent directory to the search path to enable p1_runner package imports when not installed in Python.
repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
sys.path.append(repo_root)

from p1_runner import trace as logging
from p1_runner.argument_parser import ArgumentParser, CSVAction
from p1_runner.calibration_state import CalibrationState
from p1_runner.filter_state import TightEsrifFilterState
from p1_runner.trace import HighlightFormatter

logger = logging.getLogger('point_one.extract_storage')


def import_config_loader(version):
    # Determine path to the auto-generated config loading code on S3.
    if re.match(r'^lg69t-(ap|am|ah)-.*', version):
        remote_path = f'nautilus/quectel/{version}/zip_user_config_loader.zip'
    elif re.match(r'^v\d+\.\d+\.\d+.*', version):
        remote_path = f'nautilus/atlas/{version}/zip_user_config_loader.zip'
    else:
        raise RuntimeError(f'Remote path not known for specified device type ({version}).')

    # Setup an S3 session.
    session = boto3.Session()
    credentials = session.get_credentials()
    s3 = session.resource('s3', region_name='us-west-1')
    bucket = s3.Bucket('pointone-build-artifacts')

    logger.info(f'Downloading configuration support code for software version {version}.')
    with tempfile.NamedTemporaryFile() as f, tempfile.TemporaryDirectory() as d:
        # Try to download the zip file from S3.
        try:
            bucket.download_file(remote_path, f.name)
        except botocore.exceptions.ClientError as e:
            error = e.response['Error']
            logger.error(f'Error downloading configuration support code for software version {version}: '
                         f'{error["Message"]} ({error["Code"]})')
            raise e

        # Extract the zip into the /tmp directory.
        with zipfile.ZipFile(f.name, 'r') as zip:
            zip.extractall(d)

        # Import the UserConfig class.
        parent_dir = os.path.dirname(d)
        module_name = os.path.basename(d)
        sys.path.insert(0, parent_dir)
        module = importlib.import_module(f'{module_name}.user_config_loader')
        globals()['UserConfig'] = module.UserConfig


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
Extract state and configuration data from a recorded FusionEngine data log as
binary and/or JSON files.

For example, for log abc123:
  %s abc123
will generate *.p1log and *.json files for any user configuration, filter
state, and calibration state data found in the log:
  calibration_state.{p1log, json}
  filter_state.{p1log, json}
  user_config.{p1log, json}
""" % execute_command)

    parser.add_argument(
        '-c', '--component', action=CSVAction,
        help="""\
One or more storage components to be extracted. By default, all available components will be extracted.

May be specified multiple times, or options may be specified as a comma-separated list.
             
Options include:
- calibration - Extract device calibration parameters
- filter_state - Extract the navigation engine state data
- user_config - Extract the user configuration settings
""")
    parser.add_argument(
        '-f', '--format', action=CSVAction,
        help="""\
The type of output files to generate: binary or json. By default, both file types will be generated."

May be specified multiple times, or options may be specified as a comma-separated list.
""")
    parser.add_argument(
        '-o', '--output-dir', metavar='DIR',
        help="Specify the directory where extracted output files will be stored. By default, output will be stored in "
             "the directory where the log file is located.")
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

    if options.component is None:
        options.component = ('calibration', 'filter_state', 'user_config')

    if options.format is None:
        options.format = ('binary', 'json')

    # Locate the input file and set the output directory.
    input_path, output_dir, log_id = locate_log(input_path=options.log, log_base_dir=options.log_base_dir,
                                                return_output_dir=True, return_log_id=True,
                                                load_original=True)
    if input_path is None:
        # locate_log() will log an error.
        sys.exit(1)

    if log_id is None:
        logger.info('Loading %s.' % input_path)
    else:
        logger.info('Loading %s (log ID: %s).' % (input_path, log_id))

    if options.output_dir is None:
        options.output_dir = output_dir

    # Determine the software version used to generate the log, and then download and import the matching version of the
    # user config extraction code from the published release.
    reader = MixedLogReader(input_path, message_types=(VersionInfoMessage,), return_header=False, return_bytes=True)
    try:
        version_info, _ = reader.read_next()
        import_config_loader(version_info.engine_version_str)
    except StopIteration:
        logger.error('Unable to determine software version.')
        sys.exit(2)
    except botocore.exceptions.ClientError:
        sys.exit(3)

    # Now extract data for each requested component.
    if not os.path.exists(options.output_dir):
        os.makedirs(options.output_dir)

    reader.clear_filters()
    reader.filter_in_place(key=PlatformStorageDataMessage)
    component_types = {
        'calibration': {'data_type': DataType.CALIBRATION_STATE, 'file_prefix': 'calibration_state'},
        'filter_state': {'data_type': DataType.FILTER_STATE, 'file_prefix': 'filter_state'},
        'user_config': {'data_type': DataType.USER_CONFIG, 'file_prefix': 'user_config'},
    }
    for component in options.component:
        definition = component_types[component]
        data_type = definition['data_type']
        file_prefix = definition['file_prefix']
        logger.info(f'Searching for {component} data.')
        reader.rewind()
        found = False
        for message, message_bytes in reader:
            if message.data_type != data_type:
                continue
            elif message.response == Response.NO_DATA_STORED:
                logger.warning(f'No {component} data saved on device.')
                break
            else:
                found = True

            if message.response == Response.DATA_CORRUPTED:
                logger.warning(f'Saved {component} data flagged as corrupted.')

            if 'binary' in options.format:
                path = os.path.join(options.output_dir, f'{file_prefix}.p1log')
                with open(path, 'wb') as f:
                    logger.info(f'Saving {path}...')
                    f.write(message_bytes)

            if 'json' in options.format:
                def _check_version(name, expected_version):
                    if message.data_version == expected_version:
                        return True
                    else:
                        logger.error(f'Logged {name} data version ({str(message.data_version)}) does '
                                     f'not match Python code ({str(expected_version)}). Cannot export JSON file.')
                        return False

                args = {}
                obj = None
                if component == 'user_config':
                    # Note: UserConfig is imported dynamically by import_config_loader().
                    if _check_version('UserConfig', DataVersion(*UserConfig.get_version())):
                        obj = UserConfig.deserialize(message.data)
                elif component == 'calibration':
                    if _check_version('CalibrationState', CalibrationState.VERSION):
                        obj = CalibrationState()
                        obj.unpack(message.data)
                        args = {'indent': 2, 'sort_keys': True}
                elif component == 'filter_state':
                    if _check_version('FilterState', TightEsrifFilterState.VERSION):
                        obj = TightEsrifFilterState()
                        obj.unpack(message.data)
                        args = {'indent': 2, 'sort_keys': True}
                else:
                    logger.warning(f'JSON export not supported for {component} data.')
                    obj = None

                if obj is not None:
                    path = os.path.join(options.output_dir, f'{file_prefix}.json')
                    with open(path, 'wt') as f:
                        logger.info(f'Saving {path}...')
                        f.write(obj.to_json(**args))

            break

        if not found:
            logger.warning(f'No {component} data found in log file.')


if __name__ == "__main__":
    main()
