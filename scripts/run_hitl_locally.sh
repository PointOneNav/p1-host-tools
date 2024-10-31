#!/usr/bin/env bash

set -e

export HITL_NAUTILUS_PATH=$REPO_ROOT
# CONFIGURATION, SANITY, ROOF_15_MIN, QUICK_TESTS
export HITL_TEST_TYPE="CONFIGURATION"
export HITL_NAME="run_local"
export HITL_BUILD_TYPE="ATLAS"
export HITL_DUT_VERSION=v2.2.0-rc1-236-g36fb08f2fe
#export HITL_DUT_VERSION=origin/st-develop
export JENKINS_ATLAS_LAN_IP=192.168.1.140
export JENKINS_ATLAS_BALENA_UUID=c646a700525b361b1648fe1fd7f7b997
export JENKINS_ANTENNA_LOCATION='37.84729032,-122.27868986,-7.96772104'

# python p1_hitl/hitl_runner.py -v --log-metric-values
python p1_hitl/hitl_wrapper.py -v --log-metric-values --skip-reset
# python p1_hitl/hitl_runner.py -v --list-metric-only


#python p1_hitl/hitl_runner.py -v --log-metric-values -e /logs/2024-10-18/run_local/596a841118f9497bb40a616a0bbf7c6f/env.json -p /logs/2024-10-18/run_local/596a841118f9497bb40a616a0bbf7c6f/input.raw
