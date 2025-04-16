import os
from .big_engine_interfaces import HitlBigEngineInterface


class HitlTeseoHeadingInterface(HitlBigEngineInterface):
    POLARIS_API_KEY = os.getenv('HITL_POLARIS_API_KEY')
    RUNNER_CMD = f'cd p1_fusion_engine/ && POLARIS_API_KEY={POLARIS_API_KEY} ./run_teseo_heading.sh'
