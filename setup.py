from setuptools import setup, find_packages
import pathlib

here = pathlib.Path(__file__).parent.resolve()

setup(
    name='p1-host-tools',
    version='v0.17.0',
    packages=find_packages(where='.'),
    install_requires=[
        "argparse-formatter>=1.4",
        "colorama>=0.4.4",
        "construct~=2.10.67",
        "fusion-engine-client==1.18.0",
        "gpstime>=0.6.2",
        "psutil>=5.9.4",
        "pynmea2~=1.18.0",
        "pyserial~=3.5",
        "urllib3>=1.21.1",
        "websockets>=10.1",
        # Note: Using the P1 fork of ntripstreams until fixes are mainlined.
        "ntripstreams @ https://github.com/PointOneNav/ntripstreams/archive/c26605710a53a1ebe1a16310565b5605e77228c1.zip#egg=ntripstreams",
    ],
)
