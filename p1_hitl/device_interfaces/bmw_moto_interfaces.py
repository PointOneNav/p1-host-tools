from .big_engine_interfaces import HitlBigEngineInterface


class HitlBMWMotoInterface(HitlBigEngineInterface):
    RUNNER_CMD = './p1_fusion_engine/run_fusion_engine.sh --params-path ./fusion_engine_parameters.sh ' \
                 '--pps-gpio=/dev/gpiochip0:18'
