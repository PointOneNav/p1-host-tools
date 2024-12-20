#!/usr/bin/env bash

set -e

export HITL_NAUTILUS_PATH=$REPO_ROOT
# CONFIGURATION, SANITY, ROOF_15_MIN, QUICK_TESTS, RESET_TESTS
export HITL_TEST_TYPE="CONFIGURATION"
export HITL_NAME="run_local"
export HITL_BUILD_TYPE="ATLAS"
export HITL_DUT_VERSION=9340f6e481e0bcacf4502370717b5fdb87b0c5f4
# export HITL_DUT_VERSION=origin/st-develop
export JENKINS_LAN_IP=192.168.1.176
export JENKINS_ATLAS_BALENA_UUID=c646a700525b361b1648fe1fd7f7b997
export JENKINS_ANTENNA_LOCATION='37.84729032,-122.27868986,-7.96772104'

# export JENKINS_UART1='/dev/ttyUSB0'
# export JENKINS_UART2='/dev/ttyUSB1'
# export JENKINS_RESET_RELAY='959BI:1'
# export HITL_BUILD_TYPE="LG69T_AM"
# export HITL_DUT_VERSION=lg69t-am-v0.19.0-rc1-1025-g2630269945
# source /home/jdiamond/polaris_creds.sh

# source /home/jdiamond/regression_creds.sh
# source /home/jdiamond/slack_creds.sh

export PATH="/home/jdiamond/src/usb-relay-hid/commandline/makemake:$PATH"

# python p1_hitl/hitl_runner.py -v --log-metric-values
# python -u p1_hitl/hitl_wrapper.py -v --log-metric-values --skip-reset
# python -u p1_hitl/hitl_wrapper.py -v
python -u p1_hitl/hitl_runner.py -v


#python p1_hitl/hitl_runner.py -v --log-metric-values -e /logs/2024-10-18/run_local/596a841118f9497bb40a616a0bbf7c6f/env.json -p /logs/2024-10-18/run_local/596a841118f9497bb40a616a0bbf7c6f/input.raw
# python -u p1_hitl/hitl_wrapper.py -v -p /logs/2024-12-03/jenkins-saturn2/8e94dec0365142d48789d716b25448ad
