from .big_engine_interfaces import HitlBigEngineInterface


class HitlAmazonInterface(HitlBigEngineInterface):
    RUNNER_CMD = f"""\
./p1_fusion_engine/run_fusion_engine.sh --device /dev/amazon-pgm:460800 \
--params-path ./fusion_engine_parameters.sh"""
