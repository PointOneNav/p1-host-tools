import logging
import os

from .big_engine_interfaces import HitlBigEngineInterface


class HitlBMWMotoInterface(HitlBigEngineInterface):
    DEVICE_NAME = "BMW Moto"
    VERSION_PREFIX = "bmw-moto-mic-"
    TAR_FILENAME_PREFIX = "p1_fusion_engine-"
    TAR_FILENAME_SUFFIX = "-bmw_moto_mic.tar.gz"
    RUNNER_CMD = './p1_fusion_engine/run_fusion_engine.sh --params-path ./fusion_engine_parameters.sh'
