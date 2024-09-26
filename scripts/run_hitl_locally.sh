#!/usr/bin/env bash

set -e

export HITL_NAUTILUS_PATH=$REPO_ROOT
export HITL_TEST_TYPE="CONFIGURATION"
export HITL_NAME="run_local"
export HITL_BUILD_TYPE="ATLAS"
#export HITL_DUT_VERSION=v2.1.0-1016-gf0f69c0c1c
export HITL_DUT_VERSION=origin/st-develop
export JENKINS_ATLAS_LAN_IP=192.168.1.140
export JENKINS_ATLAS_BALENA_UUID=c646a700525b361b1648fe1fd7f7b997

python p1_hitl/hitl_runner.py -v
