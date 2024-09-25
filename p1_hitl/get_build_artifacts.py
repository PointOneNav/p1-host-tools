import json
import logging
from typing import Any, Dict, Optional

import boto3

from p1_hitl.defs import BuildType

ARTIFACT_BUCKET = 'pointone-build-artifacts'
ARTIFACT_REGION = 'us-west-1'

logger = logging.getLogger('point_one.hitl.get_build_artifacts')


def get_s3_path(version_str: str, build_type: BuildType) -> str:
    # Determine path in ARTIFACT_BUCKET on S3 for build.
    if build_type is build_type.is_lg69t():
        return f'nautilus/quectel/{version_str}'
    elif build_type is BuildType.ATLAS:
        return f'nautilus/atlas/{version_str}'
    else:
        raise RuntimeError(f'Remote path not known for specified device type ({build_type.name}).')


def get_build_info(version_str: str, build_type: BuildType) -> Optional[Dict[str, Any]]:
    INFO_FILE = 'build-info.json'
    s3_path = get_s3_path(version_str, build_type) + '/' + INFO_FILE

    # Setup an S3 session.
    session = boto3.Session()
    s3 = session.resource('s3', region_name=ARTIFACT_REGION)

    logger.info(f'Downloading build info for software version {version_str}.')

    try:
        info_obj = s3.Object(ARTIFACT_BUCKET, s3_path)
        file_content = info_obj.get()['Body'].read().decode('utf-8')
    except Exception as e:
        logger.info(f"Couldn't find s3://{ARTIFACT_BUCKET}/{s3_path}. {type(e).__name__}: {str(e)}")
        return None

    return json.loads(file_content)
