#!/usr/bin/env bash

set -e

export HITL_NAUTILUS_PATH=$REPO_ROOT
# CONFIGURATION, SANITY, ROOF_15_MIN
export HITL_TEST_TYPE="ROOF_15_MIN"
export HITL_NAME="run_local"
export HITL_BUILD_TYPE="ATLAS"
#export HITL_DUT_VERSION=v2.1.0-1016-gf0f69c0c1c
export HITL_DUT_VERSION=origin/st-develop
export JENKINS_ATLAS_LAN_IP=192.168.1.140
export JENKINS_ATLAS_BALENA_UUID=c646a700525b361b1648fe1fd7f7b997
export JENKINS_ANTENNA_LOCATION='37.84729032,-122.27868986,-7.96772104'

python p1_hitl/hitl_runner.py -v --log-metric-values

#python p1_hitl/hitl_runner.py -v --log-metric-values -e /logs/2024-10-04/run_local/776543e65e5c494dba70d398a4e6e465/env.json -p /logs/2024-10-04/run_local/776543e65e5c494dba70d398a4e6e465/input.raw
