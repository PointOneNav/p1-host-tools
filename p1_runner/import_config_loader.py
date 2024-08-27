#!/usr/bin/env python3

'''
Tool for getting Python UserConfig loader class that matches device version.

See point_one/system_config/generator/config_py_idl/python_binary_loader_gen.py for UserConfig loader class
definition.

# Finding Library

## Downloading from AWS

The module containing the class can be downloaded from AWS. They are indexed by platform and version string.
For example, Atlas version 2.1 is at: "s3://pointone-build-artifacts/nautilus/atlas/v2.1.0/zip_user_config_loader.zip"

These strings can be found in the VersionInfoMessage that can be found by:
1. Queried over an FE interface from the MessageRequest(MessageType.VERSION_INFO)
2. Found in a FE stream/log by searching for the periodic VersionInfoMessage

Alternatively, they can be specified manually.

Before going to AWS, this tool will check the local cache for the desired platform/version.

## Building Locally

Instead of downloading from S3, the library can be generated from the Nautilus repo.

bazel build -c opt $BAZEL_ARGS //point_one/system_config/generator/system_config_gen/user_config_loader:zip_user_config_loader

The $BAZEL_ARGS determine which platform is being built.

# Loading Library

Once the library is found it is unzipped to a tmp directory if needed and the loader class is returned.
'''

import importlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import boto3
import botocore.exceptions
from fusion_engine_client.messages import DataVersion

# Add the parent directory to the search path to enable p1_runner package imports when not installed in Python.
repo_root = os.path.normpath(os.path.join(os.path.dirname(__file__), '..'))
default_nautilus_dir = os.path.normpath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
sys.path.append(repo_root)

from p1_runner import trace as logging
from p1_runner.argument_parser import (Action, ArgumentError, ArgumentParser,
                                       Namespace)

logger = logging.getLogger('point_one.import_config_loader')


_BUILD_TYPE_ARGS = {
    'atlas': [],
    'quectel': ['--config=quectel'],
}


_BUILD_TYPES = [v for v in _BUILD_TYPE_ARGS.keys()]


class _ValidateLoaderSource(Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if not isinstance(values, str):
            raise ArgumentError(self, f'Loader source must be a single string value not, {type(values)}')
        parts = values.split(':')
        source_types = ['infer', 'none', 'build', 'load', 'download']
        if parts[0] not in source_types:
            raise ArgumentError(self, f'Invalid loader source. {parts[0]} not in {source_types}')
        elif parts[0] in ['build', 'load', 'download']:
            if len(parts) != 2:
                raise ArgumentError(self, f'Loader source missing target: {parts[0]}:$TARGET')
        elif len(parts) != 1:
            raise ArgumentError(self, f'Loader source {parts[0]} does not expect a value.')

        if parts[0] == 'build' and parts[1] not in _BUILD_TYPES:
            raise ArgumentError(self, f'Invalid build type {parts[1]} not in {_BUILD_TYPES}')

        setattr(namespace, self.dest, values)


def get_class_from_path(load_path: Path, tmp_dir: Path) -> type:
    MODULE_NAME = 'user_config_loader'
    if not os.path.isdir(load_path):
        new_path = tmp_dir / MODULE_NAME
        logger.debug(f'Unzipping {load_path} to {new_path}.')
        os.makedirs(new_path, exist_ok=True)
        # Delete any previous files.
        shutil.rmtree(new_path, ignore_errors=True)
        with zipfile.ZipFile(load_path, 'r') as zip:
            zip.extractall(new_path)
        load_path = new_path

    # Import the UserConfig class.
    logger.debug(f'Loading user_config_loader from {load_path}.')
    parent_dir = os.path.dirname(load_path.parent)
    module_name = os.path.basename(load_path)
    sys.path.insert(0, parent_dir)
    module = importlib.import_module(f'{module_name}.user_config_loader', module_name)
    logger.info(f'Loaded UserConfig version {module.UserConfig.get_version()}.')
    return module.UserConfig


def add_config_loader_args(parser: ArgumentParser):
    group = parser.add_argument_group(
        title="User Config Loader", description="Options for loading user config data to/from JSON."
    )
    group.add_argument(
        "--user-config-loader-cache-dir",
        type=Path,
        default=tempfile.gettempdir() + "/p1_user_config_python_loader",
        help="Path to cache downloaded artifacts to.",
    )
    group.add_argument(
        "--user-config-loader-nautilus-dir",
        type=Path,
        default=default_nautilus_dir,
        help="Path to nautilus repo for Bazel build if needed.",
    )
    group.add_argument(
        "--user-config-loader-source",
        default="infer",
        action=_ValidateLoaderSource,
        help=f"""\
Where to load the definitions for the user config data. This data is device and version specific. This value can be:
  * infer - Select the library from the version information from the device. The inferred library will be downloaded.
  * none - Don't use user config loader. Throw exception if attempted.
  * build:$BUILD_TYPE - Use user config loader built from local nautilus repo. Supported build types {_BUILD_TYPES}.
  * load:$PATH - Use the loader (directory or zip) at this path.
  * download:$VERSION_STR - Explicitly specify the version string for the release to download.
""",
    )


def get_config_loader_class(args: Namespace, device_version: str) -> type:
    source_parts = args.user_config_loader_source.split(":")
    if source_parts[0] == 'none':
        raise ValueError('UserConfig loader disabled by --user-config-loader-source.')

    if source_parts[0] in ['infer', 'download']:
        if source_parts[0] == 'download':
            device_version = source_parts[1]
        module_path = download_config_loader_class(device_version, args.user_config_loader_cache_dir)
    elif source_parts[0] == 'build':
        module_path = build_local_config_loader(source_parts[1], args.user_config_loader_nautilus_dir)
    elif source_parts[0] == 'load':
        module_path = Path(source_parts[1])
    else:
        raise NotImplementedError(f'Unsupported loader source {args.user_config_loader_source}')

    return get_class_from_path(module_path, args.user_config_loader_cache_dir)


def download_config_loader_class(
    version: str, temp_dir: str = tempfile.gettempdir() + "/p1_user_config_python_loader"
) -> Path:
    # Determine path to the auto-generated config loading code on S3.
    if re.match(r'^lg69t-(ap|am|ah)-.*', version):
        remote_path = f'nautilus/quectel/{version}/zip_user_config_loader.zip'
    elif re.match(r'^v\d+\.\d+\.\d+.*', version):
        remote_path = f'nautilus/atlas/{version}/zip_user_config_loader.zip'
    else:
        raise RuntimeError(f'Remote path not known for specified device type ({version}).')

    local_path = Path(temp_dir) / remote_path

    if not local_path.exists():
        BUCKET_NAME = 'pointone-build-artifacts'
        logger.info(f'Downloading s3://{BUCKET_NAME}/{remote_path} to {local_path}.')
        # Setup an S3 session.
        session = boto3.Session()
        s3 = session.resource('s3', region_name='us-west-1')
        bucket = s3.Bucket(BUCKET_NAME)
        os.makedirs(local_path.parent, exist_ok=True)
        with tempfile.NamedTemporaryFile() as f, tempfile.TemporaryDirectory() as d:
            # Try to download the zip file from S3.
            try:
                bucket.download_file(remote_path, local_path)
            except botocore.exceptions.ClientError as e:
                error = e.response['Error']
                logger.error(
                    f'Error downloading configuration support code for software version {version}: '
                    f'{error["Message"]} ({error["Code"]})'
                )
                raise
    else:
        logger.info(f'Using cached {local_path}')

    return local_path


def build_local_config_loader(build_type: str, repo_path: str) -> Path:
    if build_type not in _BUILD_TYPES:
        raise ValueError(f'Unsupported build type {build_type}')

    logger.info(f'Building user config loader for {build_type}.')

    BAZEL_GET_BIN_DIR_CMD = ['bazel', 'info', '-c', 'opt', 'bazel-bin']

    result = subprocess.run(
        BAZEL_GET_BIN_DIR_CMD, cwd=repo_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    result.check_returncode()
    bazel_bin_path = Path(result.stdout.strip())

    build_args = _BUILD_TYPE_ARGS[build_type]

    bazel_build_cmd = (
        ['bazel', 'build', '-c', 'opt']
        + build_args
        + ['//point_one/system_config/generator/system_config_gen/user_config_loader:zip_user_config_loader']
    )

    result = subprocess.run(bazel_build_cmd, cwd=repo_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    result.check_returncode()

    return (
        bazel_bin_path
        / 'point_one/system_config/generator/system_config_gen/user_config_loader/zip_user_config_loader.zip'
    )


def test_main():
    logging.basicConfig(level=logging.INFO, format='%(message)s', stream=sys.stdout)
    logger.setLevel(logging.DEBUG)
    parser = ArgumentParser()
    add_config_loader_args(parser)
    args = parser.parse_args()
    get_config_loader_class(args, 'v2.1.0')


if __name__ == '__main__':
    test_main()
