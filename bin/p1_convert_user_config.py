#!/usr/bin/env python3

import importlib
import json
import os
import sys
from enum import Enum, auto
from pathlib import Path
from typing import Type

import construct  # For ignoring wrapper class in DeepDiff
from deepdiff import DeepDiff
from fusion_engine_client.messages.configuration import (
    ConfigurationSource, DataType, DataVersion, PlatformStorageDataMessage,
    Response)
from fusion_engine_client.parsers import (FusionEngineDecoder,
                                          FusionEngineEncoder)

# Add the parent directory to the search path to enable p1_runner package imports when not installed in Python.
repo_root = Path(os.path.dirname(__file__)).parent.absolute()
sys.path.append(str(repo_root))

from p1_runner import trace as logging
from p1_runner.argument_parser import ArgumentParser, ExtendedBooleanAction
# Example version imported just to help with type checking. The version of the class for interacting with UserConfig
# data will be loaded below with the appropriate platform type and config version based on the input file.
from user_config_loaders.platform_id_1.version_7_1.user_config_loader.user_config_loader import \
    UserConfig as UserConfigTypingClass

logger = logging.getLogger('point_one.p1_convert_user_config')


class ConversionDirection(Enum):
    TO_JSON = auto()
    TO_BINARY = auto()


def check_output(out_file: Path, options):
    if out_file.exists():
        if options.force:
            logger.info(f'Overwriting existing output path ({out_file}).')
        else:
            logger.error(
                f'Output path ({out_file}) already exists. Aborting conversion. Rerun with `--force` CLI argument to overwrite.')
            sys.exit(1)


def main():
    if getattr(sys, 'frozen', False):
        execute_command = os.path.basename(sys.executable)
    else:
        execute_command = os.path.basename(sys.executable)
        if execute_command.startswith('python'):
            execute_command += ' ' + os.path.basename(__file__)

    parser = ArgumentParser(
        usage='%s COMMAND [OPTIONS]... IN_FILE' % execute_command,
        description='Convert user configuration files between binary and JSON representations.')

    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help="Print verbose/trace debugging messages. May be specified multiple times to increase "
                             "verbosity.")

    parser.add_argument(
        'in_file',
        type=Path,
        help="The path to the file to load. Must have extension '*.json' or '*.p1log'.")
    parser.add_argument(
        '-o', '--output',
        required=False,
        type=Path,
        help="Optional path to write the converted file to. Defaults to the parent directory of `in_file`. "
             "If this is a directory, the filename will be based on the input file with new extension. For "
             "example /in_dir/bar.json -> /out_file/bar.p1log.")
    parser.add_argument(
        '-f', '--force',
        action=ExtendedBooleanAction,
        help="Overwrite an existing file at the output path instead of aborting.")

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

    # For type hinting.
    in_file: Path = options.in_file
    if options.output is None:
        out_file = in_file.parent
    else:
        out_file: Path = options.output

    if not in_file.exists():
        logger.error(f'Input file ({in_file}) not found.')
        sys.exit(1)

    # Determine the input/output file types.
    if in_file.suffix.lower() == '.json':
        direction = ConversionDirection.TO_BINARY
    elif in_file.suffix.lower() == '.p1log':
        direction = ConversionDirection.TO_JSON
    else:
        logger.error(
            f'Input file ({in_file}) has unknown file extension "{in_file.suffix}". Only "*.json" or "*.p1log" are accepted.')
        sys.exit(1)

    if out_file.is_dir():
        extension = '.p1log' if direction == ConversionDirection.TO_BINARY else '.json'
        out_file = out_file / (in_file.stem + extension)

    # Parse the input file and determine the platform type and version of the saved UserConfig data.
    if direction == ConversionDirection.TO_BINARY:
        with open(in_file, 'r') as fd:
            try:
                json_data = json.load(fd)
            except Exception as e:
                logger.error(f'Could not parse {in_file}: {e}')
                sys.exit(1)

        if '__version' not in json_data:
            logger.error(f'{in_file} is missing required field "__version"')
            sys.exit(1)
        version_str = json_data['__version']
        if '__platform_id' not in json_data:
            logger.error(f'{in_file} is missing required field "__platform_id"')
            sys.exit(1)
        platform_id = json_data['__platform_id']
    else:
        with open(in_file, 'rb') as fd:
            binary_data = fd.read()
        decoder = FusionEngineDecoder()
        messages = decoder.on_data(binary_data)
        if len(messages) != 1:
            logger.error(f'{in_file} did not contain binary FusionEngine data.')
            sys.exit(1)

        payload = messages[0][1]
        if not isinstance(payload, PlatformStorageDataMessage):
            logger.error(f'{in_file} did not contain PlatformStorageDataMessage.')
            sys.exit(1)

        version_str = str(payload.data_version)
        platform_id = payload.flags
        if platform_id == 0:
            logger.error(f'{in_file} did not specify a platform ID. Device firmware not supported by this tool.')
            sys.exit(1)

    # Import the UserConfig class.
    user_config_dir = Path(repo_root) / 'user_config_loaders' / \
        f'platform_id_{platform_id}' / f'version_{version_str.replace(".", "_")}'
    if not user_config_dir.exists():
        logger.error(
            f'The UserConfig support for platform ID {platform_id} and version {version_str} is not known'
            f'({user_config_dir} not found). A newer p1-host-tools release may have added support.')
        sys.exit(1)

    logger.info(f'Loading UserConfig support for platform ID {platform_id} and version {version_str}.')

    sys.path.insert(0, str(user_config_dir))
    #module = importlib.import_module(f'user_config_loader.user_config_loader', 'user_config_loader')
    module = importlib.import_module(f'user_config_loader')
    logger.info(f'Loaded UserConfig support version {module.UserConfig.get_version()}.')
    UserConfig: Type[UserConfigTypingClass] = module.UserConfig

    # Convert to binary or JSON and write the result to disk.
    if direction == ConversionDirection.TO_BINARY:
        loaded_config_data = {k: v for k, v in json_data.items() if not k.startswith('__')}
        try:
            user_config = UserConfig.from_dict(loaded_config_data)
        except Exception as e:
            logger.error(f'Error parsing {in_file}: {e}')
            sys.exit(1)
        full_config_data = user_config.to_dict()
        conf_diff = DeepDiff(
            full_config_data,
            loaded_config_data,
            ignore_nan_inequality=True,
            ignore_numeric_type_changes=True,
            math_epsilon=0.00001,
            ignore_type_in_groups=[(list, construct.lib.ListContainer)],
        )

        if len(conf_diff) > 0:
            logger.error(f'The fields in {in_file} do not match full set used by UserConfig:\n{conf_diff.pretty()}')
            sys.exit(1)
        else:
            encoder = FusionEngineEncoder()
            try:
                config_data = UserConfig.serialize(user_config)
            except Exception as e:
                logger.error(f'JSON to binary conversion failed: {e}')
                sys.exit(1)
            storage_message = PlatformStorageDataMessage()
            major, minor = [int(i) for i in version_str.split('.')]
            storage_message.data_version = DataVersion(major, minor)
            storage_message.data_type = DataType.USER_CONFIG
            storage_message.response = Response.OK
            storage_message.flags = platform_id
            storage_message.source = ConfigurationSource.SAVED
            storage_message.data = config_data
            wrapped_data = encoder.encode_message(storage_message)

            check_output(out_file, options)
            logger.info(f'Writing binary UserConfig to {out_file}.')
            with open(out_file, 'wb') as fd:
                fd.write(wrapped_data)
    else:
        user_config = UserConfig.deserialize(payload.data)
        data = user_config.to_json()
        check_output(out_file, options)
        logger.info(f'Writing JSON UserConfig to {out_file}.')
        with open(out_file, 'w') as fd:
            fd.write(data)

    sys.exit(0)


if __name__ == "__main__":
    main()
