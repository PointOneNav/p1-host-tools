import logging
import os

from .big_engine_interfaces import HitlBigEngineInterface

SSH_USERNAME = "pointone"
SSH_KEY_PATH = "/home/pointone/.ssh/id_ed25519"

logger = logging.getLogger('point_one.hitl.bmw_moto_interface')

class HitlBMWMotoInterface(HitlBigEngineInterface):
    LOGGER = logging.getLogger('point_one.hitl.bmw_moto_interface')
    POLARIS_API_KEY = os.getenv('HITL_POLARIS_API_KEY')
    OUTPUT_PORT = 30200
    DIAGNOSTIC_PORT = 30202
    DEVICE_NAME = "BMW Moto"
    VERSION_PREFIX = "bmw-moto-mic-"
    TAR_FILENAME_PREFIX = "p1_fusion_engine-"
    TAR_FILENAME_SUFFIX = "-bmw_moto_mic.tar.gz"
    RUNNER_CMD = f"""
./p1_fusion_engine/run_fusion_engine.sh --params-path ./fusion_engine_parameters.sh"""