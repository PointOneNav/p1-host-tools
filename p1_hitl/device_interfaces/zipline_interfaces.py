import logging
import os

from .big_engine_interfaces import HitlBigEngineInterface

class HitlZiplineInterface(HitlBigEngineInterface):
    POLARIS_API_KEY = os.getenv('HITL_POLARIS_API_KEY')
    OUTPUT_PORT = 30200
    DIAGNOSTIC_PORT = 30202
    DEVICE_NAME = "Zipline"
    VERSION_PREFIX = "zipline-"
    TAR_FILENAME_PREFIX = "p1_fusion_engine-"
    TAR_FILENAME_SUFFIX = "-zipline.tar.gz"
    RUNNER_CMD = f"""\
./p1_fusion_engine/run_fusion_engine.sh --lg69t-device /dev/zipline-lg69t \
--device-id hitl --cache ./p1_fusion_engine/cache --tcp-output-port {OUTPUT_PORT} \
--tcp-diagnostics-port {DIAGNOSTIC_PORT} --corrections-source polaris --polaris {POLARIS_API_KEY}"""
