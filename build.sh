#!/usr/bin/env bash

IMAGE_NAME="pschmitt/gcal-import-ics"

usage() {
  echo "Usage: $0"
}

array_join() {
  local IFS="$1"
  shift
  echo "$*"
}

get_available_architectures() {
  local image="$1"
  local tag="${2:-latest}"

  docker buildx imagetools inspect --raw "${image}:${tag}" | \
    jq -r '.manifests[].platform | .os + "/" + .architecture + "/" + .variant' | \
    sed 's#/$##' | sort
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]
then
  set -ex

  cd "$(readlink -f "$(dirname "$0")")" || exit 9

  # shellcheck disable=2207
  platforms=($(get_available_architectures python 3-alpine))

  PUSH_IMAGE=true
  BUILD_TYPE=manual

  if [[ "$GITHUB_ACTIONS" == "true" ]]
  then
    BUILD_TYPE=github
  fi

  docker buildx build \
    --platform "$(array_join "," "${platforms[@]}")" \
    --output "type=image,push=${PUSH_IMAGE}" \
    --no-cache \
    --label=built-by=pschmitt \
    --label=build-type="$BUILD_TYPE" \
    --label=built-on="$HOSTNAME" \
    --tag "${IMAGE_NAME}:latest" \
    .
fi
