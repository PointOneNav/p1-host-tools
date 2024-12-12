import logging
import os

from .big_engine_interfaces import HitlBigEngineInterface

class HitlBMWMotoInterface(HitlBigEngineInterface):
    POLARIS_API_KEY = os.getenv('HITL_POLARIS_API_KEY')
    OUTPUT_PORT = 30200
    DIAGNOSTIC_PORT = 30202
    DEVICE_NAME = "BMW Moto"
    VERSION_PREFIX = "bmw-moto-mic-"
    TAR_FILENAME_PREFIX = "p1_fusion_engine-"
    TAR_FILENAME_SUFFIX = "-bmw_moto_mic.tar.gz"
    RUNNER_CMD = f"""\
./p1_fusion_engine/run_fusion_engine.sh --params-path ./fusion_engine_parameters.sh"""
