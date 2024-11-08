import json
import logging
import re
from typing import IO, Any, Dict, Optional, BinaryIO

import boto3

from p1_hitl.defs import DeviceType
from p1_runner.exception_utils import exception_to_str

ARTIFACT_BUCKET = 'pointone-build-artifacts'
ARTIFACT_REGION = 'us-west-1'

logger = logging.getLogger('point_one.hitl.get_build_artifacts')


def get_s3_path(version_str: str, build_type: DeviceType) -> str:
    # Determine path in ARTIFACT_BUCKET on S3 for build.
    if build_type.is_lg69t():
        return f'nautilus/quectel/{version_str}'
    elif build_type is DeviceType.ATLAS:
        return f'nautilus/atlas/{version_str}'
    else:
        raise RuntimeError(f'Remote path not known for specified device type ({build_type.name}).')


def get_build_info(version_str: str, build_type: DeviceType) -> Optional[Dict[str, Any]]:
    INFO_FILE = 'build-info.json'
    s3_path = get_s3_path(version_str, build_type) + '/' + INFO_FILE
    logger.info(f'Downloading build info for software version {version_str}.')

    # Setup an S3 session.
    session = boto3.Session()
    s3 = session.resource('s3', region_name=ARTIFACT_REGION)

    try:
        info_obj = s3.Object(ARTIFACT_BUCKET, s3_path)
        file_content = info_obj.get()['Body'].read().decode('utf-8')
    except Exception as e:
        logger.error(f"Couldn't find s3://{ARTIFACT_BUCKET}/{s3_path}. {exception_to_str(e)}")
        return None

    return json.loads(file_content)


def download_file(fd: IO[bytes], aws_path: str, file_re:str) -> bool:
    if not aws_path.endswith('/'):
        aws_path += '/'

    bucket = ARTIFACT_BUCKET
    if aws_path.startswith('s3://'):
        parts = aws_path.split('/')
        bucket = parts[2]
        aws_path = '/'.join(parts[3:])

    try:
        # List objects within the specified prefix
        s3 = boto3.client('s3')
        response = s3.list_objects_v2(Bucket=bucket, Prefix=aws_path)
    except Exception as e:
        logger.error(f"Couldn't find s3://{bucket}/{aws_path}. {exception_to_str(e)}")
        return False

    # Check if any objects exist
    if 'Contents' in response:
        for obj in response['Contents']:
            path = obj['Key'][len(aws_path):]
            if re.match(file_re, path):
                s3.download_fileobj(bucket, obj['Key'], fd)
                return True

    print(f"No objects found in s3://{bucket}/{aws_path} with the specified regex: '{file_re}'.")
    return False

def _test_main():
    import io
    fd = io.BytesIO()
    download_file(fd, 's3://pointone-build-artifacts/nautilus/quectel/lg69t-am-v0.19.0-rc1-1006-g842ecae958-dirty', r'.*\.p1fw')

if __name__ == '__main__':
    _test_main()
