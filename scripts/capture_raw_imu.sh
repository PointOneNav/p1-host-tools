#!/usr/bin/env bash
set -e

function egress {
    JOB_PIDS=$(jobs -p)
    if [[ -n "${JOB_PIDS}" ]]; then
        kill $JOB_PIDS 2> /dev/null
    fi
}

trap egress EXIT

OUT_FILE=/tmp/p1_imu.p1log
OUT_IDX_FILE=/tmp/p1_imu.p1i

################################################################################
# Handle Arguments
################################################################################

UUID=
IP=
PORT=30202

parse_args() {
    # Process command line arguments.
    args=()
    while [ "$1" != "" ]; do
    case $1 in
        -h | --help)
            show_usage
            exit 0
            ;;
        -u=* | --uuid=*)
            UUID="${1/*"="/}"
            ;;
        --)
            shift
            args+=($*)
            break
            ;;
        *)
            if [[ "$1" == -* ]]; then
              args+=($1)
            else
              args+=("$1")
            fi
            ;;
    esac
    shift
    done
}

parse_args "$@"
set -- "${args[@]}"

if [[ -n "$UUID" ]]; then
    echo "Connecting to Balena UUID through tunnel: $UUID"
    balena tunnel $UUID -p $PORT:$PORT &
    IP="127.0.0.1"
    # Wait for tunnel to start
    sleep 3
elif [[ -n "$1" ]]; then
    IP="$1"
    echo "Connecting to device IP: $IP"
else
    echo "Specify port or Balena UUID"
    exit 1
fi

rm -f $OUT_IDX_FILE

# Enable raw IMU on diagnostic port 30202 (TCP2)
./bin/config_tool.py --device=tcp://$IP:$PORT apply current message_rate FE RawIMUOutput ON

echo "Logging a minute of IMU data to $OUT_FILE ."
timeout 60 netcat  $IP $PORT > $OUT_FILE
