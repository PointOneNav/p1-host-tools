import io
import json
import logging
import os
import subprocess
import sys
from argparse import ArgumentParser
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Dict

import boto3

# Add the parent directory to the search path to enable p1_runner package imports when not installed in Python.
repo_root = Path(__file__).parents[1].resolve()
DEFAULT_NAUTILUS_DIR = Path(__file__).parents[2].resolve()

S3_DEFAULT_INGEST_BUCKET = 'pointone-ingest-landingpad'
S3_DEFAULT_REGION = 'us-west-1'
META_FILE = "drive_test_metadata.json"
MANIFEST_FILE = 'maniphest.json'

logger = logging.getLogger('point_one.p1_test_automation.add_novatel_log')

s3_client = boto3.client('s3', region_name=S3_DEFAULT_REGION)


def download_to_memory(s3_key) -> bytes:
    file_stream = io.BytesIO()
    s3_client.download_fileobj(S3_DEFAULT_INGEST_BUCKET, s3_key, file_stream)
    file_stream.seek(0)
    return file_stream.read()


def build_csv_gen(repo_path: Path) -> Path:
    logger.info(f'Building csv_generator.')

    BAZEL_GET_BIN_DIR_CMD = ['bazel', 'info', '-c', 'opt', 'bazel-bin']

    result = subprocess.run(
        BAZEL_GET_BIN_DIR_CMD, cwd=repo_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
    )
    if result.returncode != 0:
        raise RuntimeError(
            f'Bazel info failed. Is "{repo_path}" a valid nautilus repo? Change with --nautilus-dir.\n{result.args}:\n{result.stderr}')

    bazel_bin_path = Path(result.stdout.strip())

    bazel_build_cmd = ['bazel', 'build', '-c', 'opt', '//point_one/logger:csv_generator']

    result = subprocess.run(bazel_build_cmd, cwd=repo_path, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode != 0:
        raise RuntimeError(f'Bazel build failed.\n{result.args}:\n{result.stdout}')

    return (
        bazel_bin_path
        / 'point_one/logger/csv_generator'
    )


def generate_csv(novatel_log_file: Path, csv_generator_binary: Path) -> TemporaryDirectory:
    logger.info(f'Converting Novatel data to CSV.')

    # Make dummy directory for CSV conversion. Tool expects a maniphest.json file.
    tmp_dir = TemporaryDirectory()
    tmp_dir_path = Path(tmp_dir.name)
    os.symlink(novatel_log_file, tmp_dir_path / 'novatel.nov')
    open(tmp_dir_path / MANIFEST_FILE, 'w').write('''\
{
    "channels": ["novatel.nov"]
}
''')

    cmd = [str(csv_generator_binary), '--log=' + tmp_dir.name, '--mode=raw_novatel']
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode != 0:
        raise RuntimeError(f'CSV conversion failed.\n{result.args}:\n{result.stdout}')

    return tmp_dir


def main():
    parser = ArgumentParser()
    parser.add_argument(
        '-v',
        '--verbose',
        action='count',
        default=0,
        help="Print verbose/trace debugging messages. May be specified multiple times to increase verbosity.",
    )
    parser.add_argument(
        "--nautilus-dir",
        type=Path,
        default=DEFAULT_NAUTILUS_DIR,
        help="Path to nautilus repo for Bazel build if needed.",
    )
    parser.add_argument(
        "--csv-generator-binary",
        type=Path,
        help="Path to nautilus repo for Bazel build if needed.",
    )
    parser.add_argument('novatel_log', help="Novatel binary reference log to process and upload.")
    parser.add_argument('key_for_log_in_drive', help="The full S3 key for one of the logs in the drive.\n"
                        "Ex. '2024-04-04/p1-lexus-rack-2/a0a0ff472ea342809d05380d8fe54399'")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', stream=sys.stdout
    )

    # Check the key for the S3 log to upload to.
    key_split = args.key_for_log_in_drive.split('/')
    if len(key_split) < 3:
        logger.error(
            f'Key had unexpected prefix. Expecting S3 key like "2024-04-04/p1-lexus-rack-2/a0a0ff472ea342809d05380d8fe54399".')
        exit(1)
    elif len(key_split) > 3:
        args.key_for_log_in_drive = '/'.join(key_split[:3])
        logger.warning(
            f'Key had unexpected prefix. Expecting S3 key like "2024-04-04/p1-lexus-rack-2/a0a0ff472ea342809d05380d8fe54399". Only using "{args.key_for_log_in_drive}".')

    # Get drive test metadata.
    try:
        meta_key = args.key_for_log_in_drive + '/' + META_FILE
        drive_meta_data = download_to_memory(meta_key)
    except:
        logger.error(
            f'Could not find "S3://{S3_DEFAULT_INGEST_BUCKET}/{meta_key}". Make sure this log was taken as part of a drive test collection.')
        exit(1)
    drive_meta = json.loads(drive_meta_data.decode('utf-8'))

    # Get the binary for performing Novatel conversions.
    if args.csv_generator_binary is None:
        csv_generator_binary = build_csv_gen(args.nautilus_dir)
    else:
        csv_generator_binary = args.csv_generator_binary
    logger.info(f'Using {csv_generator_binary} to convert Novatel data.')

    # Convert Novatel data to CSV.
    conversion_dir = generate_csv(args.novatel_log, csv_generator_binary)
    converted_file = Path(conversion_dir.name) / 'data/novatel.csv'

    # Upload to S3
    logger.info(f'Updating S3 log {args.key_for_log_in_drive}.')
    novatel_upload_key = args.key_for_log_in_drive + '/data/novatel.csv'
    s3_client.upload_file(converted_file, S3_DEFAULT_INGEST_BUCKET, novatel_upload_key)

    # Update the metadata to include the new reference
    drive_meta['has_novatel_reference'] = True
    file_stream = io.BytesIO()
    meta_string = json.dumps(drive_meta, indent=2)
    file_stream.write(meta_string.encode())
    file_stream.seek(0)
    s3_client.upload_fileobj(file_stream, S3_DEFAULT_INGEST_BUCKET, meta_key)


if __name__ == '__main__':
    main()
