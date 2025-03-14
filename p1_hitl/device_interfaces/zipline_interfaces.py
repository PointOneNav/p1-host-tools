import os
from pathlib import Path

from .big_engine_interfaces import (DIAGNOSTIC_PORT, OUTPUT_PORT,
                                    HitlBigEngineInterface)


class HitlZiplineInterface(HitlBigEngineInterface):
    POLARIS_API_KEY = os.getenv('HITL_POLARIS_API_KEY')
    DEVICE_NAME = "Zipline"
    VERSION_PREFIX = "zipline-"
    TAR_FILENAME_PREFIX = "p1_fusion_engine-"
    TAR_FILENAME_SUFFIX = "-zipline.tar.gz"
    LOG_DIR = Path('/home/pointone/p1_fusion_engine/cache/logs')
    RUNNER_CMD = f"""\
./p1_fusion_engine/run_fusion_engine.sh --lg69t-device /dev/zipline-lg69t \
--device-id hitl --cache ./p1_fusion_engine/cache --tcp-output-port {OUTPUT_PORT} \
--tcp-diagnostics-port {DIAGNOSTIC_PORT} --polaris {POLARIS_API_KEY} \
--log-at-startup"""
