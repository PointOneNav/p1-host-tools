#!/bin/bash

set -e

BRANCH=$1

PRODUCT=p1-host-tools

if [[ -z ${BRANCH} ]]; then
    BRANCH=`git rev-parse --abbrev-ref HEAD`
fi

if [[ -z ${GITHUB_TOKEN} ]]; then
    echo "GITHUB_TOKEN environment variable is not set."
    exit 1
fi

echo "Starting build for ${PRODUCT} on branch ${BRANCH}."

curl -X POST -H "Accept: application/vnd.github+json" -H "Authorization: Bearer ${GITHUB_TOKEN}" \
     https://api.github.com/repos/PointOneNav/p1-host-tools/actions/workflows/release.yml/dispatches \
     -d "{\"ref\":\"${BRANCH}\"}"
