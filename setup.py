import pathlib
import sys

from setuptools import find_packages, setup

here = pathlib.Path(__file__).parent.resolve()

requirements = [
    "argparse-formatter>=1.4",
    "colorama>=0.4.4",
    "construct~=2.10.67",
    "deepdiff>=8.0.1",
    # Install fusion-engine-client from the internal Point One GitHub repository.
    "fusion-engine-client[all] @ git+ssh://git@github.com/PointOneNav/fusion-engine-client-internal@b0546b6558febae11ac3f55d60ee00ec7507b88a#subdirectory=python",
    "psutil>=5.9.4",
    "pynmea2~=1.18.0",
    "pyserial~=3.5",
    "urllib3>=1.21.1",
    # Note: Using the P1 fork of ntripstreams until fixes are mainlined.
    "ntripstreams @ https://github.com/PointOneNav/ntripstreams/archive/d2c8b8e55ae64e440e58bccf290e4d14095aa6e4.zip#egg=ntripstreams",
]

dev_requirements = [
    "autopep8~=2.3.1",
    "isort~=5.13.2",
    "pytest",
]

internal_only_requirements = [
    # NOTE: balena-sdk==15.0.0 introduced breaking changes migrating to API v7. Need
    # to update ~/.balena/balena.cfg as well as fix "should_be_running__release"
    "balena-sdk==14.5.0",
    "remi>=2022.7.27",
    "boto3>=1.34",
    "pydantic>=2.9.1",
    "jenkinsapi>=0.3.13",
    "slack_sdk>=3.33.1",
    "paramiko>=3.5.0",
    "scp>=0.15.0",
]

all_requirements = requirements + dev_requirements + internal_only_requirements

if sys.version_info >= (3, 7):
    requirements.append("websockets>=10.1")

setup(
    name='p1-host-tools',
    version='v0.30.1',
    packages=find_packages(where='.'),
    install_requires=list(all_requirements),
    extras_require={
        # Kept for backwards compatibility.
        'all': [],
        'dev': [],
    },
)
