import logging
import os

from .big_engine_interfaces import HitlBigEngineInterface


class HitlAmazonInterface(HitlBigEngineInterface):
    DEVICE_NAME = "Amazon"
    VERSION_PREFIX = "amazon-fleetedge-1-"
    TAR_FILENAME_PREFIX = "p1_fusion_engine-"
    TAR_FILENAME_SUFFIX = "-amazon_fleetedge_1.tar.gz"
    RUNNER_CMD = f"""\
./p1_fusion_engine/run_fusion_engine.sh --device /dev/amazon-pgm:460800 \
--params-path ./fusion_engine_parameters.sh"""
