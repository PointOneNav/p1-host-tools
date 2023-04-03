#!/usr/bin/env bash

set -e

# Find the directory of this file, following symlinks.
#
# Reference:
# - https://stackoverflow.com/questions/59895/how-to-get-the-source-directory-of-a-bash-script-from-within-the-script-itself
get_parent_dir() {
    local SOURCE="${BASH_SOURCE[0]}"
    while [ -h "$SOURCE" ]; do
        local DIR="$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )"
        SOURCE="$(readlink "$SOURCE")"
        [[ $SOURCE != /* ]] && SOURCE="$DIR/$SOURCE"
    done

    local PARENT_DIR="$( cd -P "$( dirname "$SOURCE" )" >/dev/null 2>&1 && pwd )"
    echo "${PARENT_DIR}"
}

PARENT_DIR=$(get_parent_dir)

# Switch directory to this script's parent directory, assuming it's within the
# Nautilus git repo. That way, if we're doing an out-of-tree build, the git
# commands below still work correctly.
pushd "${PARENT_DIR}" >/dev/null

# Tag format: v0.1
VERSION_STR=$(git describe --always --tags --dirty --match v?.*)

# If we can't find a tag above, fall back to a default version string format:
#   v0.0.0-x-g<commit>[-dirty]
if [[ "${VERSION_STR}" != "v"* ]]; then
  # Extract the commit hash and dirty status for the current commit:
  #   v0.11.1-41-g2c244a4b14-dirty --> 2c244a4b14-dirty
  COMMIT_INFO=$(git describe --dirty | perl -pe 's/.*-g(.*)/$1/')
  VERSION_STR="$v0.0.0-x-g${COMMIT_INFO}"
fi

# Set EXPORT_TO_GITHUB_ENV environment variable outside of this if statement for github action.
if [[ "${EXPORT_TO_GITHUB_ENV}" == "true" ]]; then
  PARENT_GIT_TAG=$(echo "${VERSION_STR}" | perl -pe 's/(v([0-9.]+(-rc[0-9]+)?)).*/$1/')
  VERSION_NUMBER=$(echo "${VERSION_STR}" | perl -pe 's/v([0-9.]+(-rc[0-9]+.*)?)/$1/')
  echo "VERSION_NUMBER=${VERSION_NUMBER}" | tee -a $GITHUB_ENV
  echo "VERSION_STR=${VERSION_STR}" | tee -a $GITHUB_ENV
  echo "PARENT_GIT_TAG=${PARENT_GIT_TAG}" | tee -a $GITHUB_ENV
else
  echo "${VERSION_STR}"
fi

popd >/dev/null
