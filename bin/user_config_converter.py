#!/usr/bin/env python3

from enum import Enum, auto
import importlib
import json
import os
from pathlib import Path
import sys
from typing import Type

from fusion_engine_client.messages.configuration import PlatformStorageDataMessage
from fusion_engine_client.parsers.decoder import FusionEngineDecoder

# Add the parent directory to the search path to enable p1_runner package imports when not installed in Python.
repo_root = Path(os.path.dirname(__file__)).parent.absolute()
sys.path.append(str(repo_root))

# Example version imported just to help with type checking
from user_config_loaders.platform_1.version_7_1.user_config_loader.user_config_loader import UserConfig as UserConfigTypingClass

from p1_runner import trace as logging
from p1_runner.argument_parser import ArgumentParser

logger = logging.getLogger('point_one.user_config_converter')


class ConversionDirection(Enum):
    TO_JSON = auto()
    TO_BINARY = auto()


def main():
    if getattr(sys, 'frozen', False):
        execute_command = os.path.basename(sys.executable)
    else:
        execute_command = os.path.basename(sys.executable)
        if execute_command.startswith('python'):
            execute_command += ' ' + os.path.basename(__file__)

    parser = ArgumentParser(
        usage='%s COMMAND [OPTIONS]... IN_FILE OUT_FILE' % execute_command,
        description='Convert user configuration files between binary and JSON representations.')

    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help="Print verbose/trace debugging messages. May be specified multiple times to increase "
                             "verbosity.")

    parser.add_argument(
        'in_file',
        type=Path,
        help="The path to the file to load. Must have extension '*.json' or '*.p1log'.")
    parser.add_argument(
        'out_file',
        type=Path,
        help="The path to write the converter file to. If this is a directory, the filename will be based on the input file with new extension. For example /in_dir/bar.json -> /out_file/bar.p1log.")

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
    out_file: Path = options.out_file

    if not in_file.exists():
        logger.error(f'in_file "{in_file}" not found.')
        exit(1)

    if in_file.suffix.lower() == '.json':
        direction = ConversionDirection.TO_BINARY
    elif in_file.suffix.lower() == '.p1log':
        direction = ConversionDirection.TO_JSON
    else:
        logger.error(f'in_file has unknown file extension "{in_file.suffix}". Only "*.json" or "*.p1log" are accepted.')
        exit(1)

    if out_file.is_dir():
        if direction == ConversionDirection.TO_BINARY:
            extension = '.p1log'
        else:
            extension = '.json'

        out_file = out_file / (in_file.stem + extension)

    if direction == ConversionDirection.TO_BINARY:
        with open(in_file, 'r') as fd:
            try:
                json_data = json.load(fd)
            except Exception as e:
                logger.error(f'Could not parse {in_file}: {e}')
                exit(1)

        if '__version' not in json_data:
            logger.error(f'{in_file} is missing required field "__version"')
            exit(1)
        version_str = json_data['__version']
        if '__platform_id' not in json_data:
            logger.error(f'{in_file} is missing required field "__platform_id"')
            exit(1)
        platform_id = json_data['__platform_id']
    else:
        with open(in_file, 'rb') as fd:
            binary_data = fd.read()
        decoder = FusionEngineDecoder()
        messages = decoder.on_data(binary_data)
        if len(messages) != 1:
            logger.error(f'{in_file} did not contain binary FusionEngine data.')
            exit(1)

        payload = messages[0][1]
        if not isinstance(payload, PlatformStorageDataMessage):
            logger.error(f'{in_file} did not contain PlatformStorageDataMessage.')
            exit(1)

        version_str = str(payload.data_version)
        platform_id = str(payload.flags)
        if platform_id == 0:
            logger.error(f'{in_file} did not specify a platform ID. Device firmware not supported by this tool.')
            exit(1)

    user_config_dir = Path(repo_root) / 'user_config_loaders' / \
        f'platform_{platform_id}' / f'version_{version_str.replace(".", "_")}'
    if not user_config_dir.exists():
        logger.error(
            f'The UserConfig for Platform ID {platform_id} and version {version_str} is not known ({user_config_dir} not found). A newer p1-host-tools release may have added support.')
        exit(1)

    logger.info(f'Loading UserConfig for Platform ID {platform_id} and version {version_str}.')

    # Import the UserConfig class.
    sys.path.insert(0, str(user_config_dir))
    module = importlib.import_module(f'user_config_loader.user_config_loader', 'user_config_loader')
    logger.info(f'Loaded UserConfig version {module.UserConfig.get_version()}.')
    UserConfig: Type[UserConfigTypingClass] = module.UserConfig

    if direction == ConversionDirection.TO_BINARY:
        user_config = UserConfig()
        unused = user_config.update(json_data)
        # Ignore metadata fields
        unused = {k: v for k, v in unused.items() if not k.startswith('__')}
        if len(unused) > 0:
            logger.error(f'Some JSON fields not valid UserConfig entries: {unused}')
            exit(1)
        else:
            data = UserConfig.serialize(user_config)
            logger.info(f'Writing binary UserConfig to {out_file}.')
            with open(out_file, 'wb') as fd:
                fd.write(data)
    else:
        user_config = UserConfig.deserialize(payload.data)
        data = user_config.to_json()
        logger.info(f'Writing JSON UserConfig to {out_file}.')
        with open(out_file, 'w') as fd:
            fd.write(data)


if __name__ == "__main__":
    main()
