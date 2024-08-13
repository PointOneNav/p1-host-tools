import sys
from setuptools import setup, find_packages
import pathlib

here = pathlib.Path(__file__).parent.resolve()

requirements = [
    "argparse-formatter>=1.4",
    "colorama>=0.4.4",
    "construct~=2.10.67",
    "fusion-engine-client==1.23.4",
    "psutil>=5.9.4",
    "pynmea2~=1.18.0",
    "pyserial~=3.5",
    "urllib3>=1.21.1",
    # Note: Using the P1 fork of ntripstreams until fixes are mainlined.
    "ntripstreams @ https://github.com/PointOneNav/ntripstreams/archive/d2c8b8e55ae64e440e58bccf290e4d14095aa6e4.zip#egg=ntripstreams",
    # Internal p1_test_automation dependencies.
    "balena-sdk>=14.2.0",
    "deepdiff>6.7",
    "remi>=2022.7.27",
    "boto3>=1.34",
]

if sys.version_info >= (3, 7):
    requirements.append("websockets>=10.1")

setup(
    name='p1-host-tools',
    version='v0.18.4',
    packages=find_packages(where='.'),
    install_requires=requirements,
)
