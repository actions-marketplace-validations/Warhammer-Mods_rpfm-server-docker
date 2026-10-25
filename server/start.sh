#!/usr/bin/env bash
set -euo pipefail

if [[ "${RUNNER_OS}" != Linux ]]; then
    echo 'RPFM Server requires a Linux runner with Docker.' >&2
    exit 1
fi
if [[ ! "${RPFM_INPUT_PORT}" =~ ^[0-9]{1,5}$ ]] || (( 10#${RPFM_INPUT_PORT} < 1024 || 10#${RPFM_INPUT_PORT} > 65535 )); then
    echo 'port must be an integer from 1024 to 65535' >&2
    exit 1
fi
if [[ "${RPFM_BUILD_FROM_SOURCE}" != true && "${RPFM_BUILD_FROM_SOURCE}" != false ]]; then
    echo 'build-from-source must be true or false' >&2
    exit 1
fi
if [[ -n "${RPFM_INPUT_IMAGE}" && "${RPFM_BUILD_FROM_SOURCE}" == true ]]; then
    echo 'Choose image or build-from-source, not both.' >&2
    exit 1
fi
name="rpfm-${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}-${RANDOM}"
image="${RPFM_INPUT_IMAGE}"
config=''
lock=''
started=false
success=false
cleanup() {
    [[ -z "$lock" ]] || rm -f "$lock"
    if [[ "$success" != true ]]; then
        if [[ "$started" == true ]]; then
            docker logs "$name" >&2 || true
            docker rm --force "$name" >/dev/null || true
        fi
        # This directory was created by this invocation under RUNNER_TEMP.
        [[ -z "$config" ]] || rmdir "$config" 2>/dev/null || true
    fi
}
trap cleanup EXIT
if [[ "${RPFM_BUILD_FROM_SOURCE}" == true ]]; then
    image="local/${name}:source"
    docker build --tag "$image" "$RPFM_ACTION_PATH"
elif [[ -z "$image" ]]; then
    if [[ "${RUNNER_ARCH}" != X64 ]]; then
        echo 'The published image supports Linux x86-64. Use build-from-source on other architectures.' >&2
        exit 1
    fi
    lock=$(mktemp "${RUNNER_TEMP}/rpfm-image.XXXXXX.json")
    url=$(python3 "${RPFM_ACTION_PATH}/image.py" url)
    if ! curl --fail --silent --show-error --location --retry 3 --max-time 60 \
        --proto '=https' --max-filesize 65536 "$url" --output "$lock"; then
        rm -f "$lock"
        echo 'Published image metadata is unavailable. Finish the image release, or set build-from-source: true.' >&2
        exit 1
    fi
    image=$(python3 "${RPFM_ACTION_PATH}/image.py" resolve --lock "$lock")
    rm -f "$lock"
    docker pull "$image"
else
    # Keep support for locally built images as well as custom registry images.
    if ! docker image inspect "$image" >/dev/null 2>&1; then
        docker pull "$image"
    fi
fi
config=$(mktemp -d "${RUNNER_TEMP}/rpfm-config.XXXXXX")
url="http://127.0.0.1:${RPFM_INPUT_PORT}"
docker run --detach --name "$name" \
    --user "$(id -u):$(id -g)" \
    --env HOME=/tmp \
    --publish "127.0.0.1:${RPFM_INPUT_PORT}:45128" \
    --volume "${config}:/config" \
    --volume "${GITHUB_WORKSPACE}:/work" \
    "$image"
started=true
ready=false
for attempt in {1..60}; do
    if curl --fail --silent --max-time 5 "${url}/version" >/dev/null; then
        ready=true
        break
    fi
    if [[ "$(docker inspect --format '{{.State.Running}}' "$name")" != true ]]; then
        break
    fi
    sleep 1
done
if [[ "$ready" != true ]]; then
    echo 'RPFM did not become ready.' >&2
    exit 1
fi
curl --fail --silent --max-time 5 "${url}/version"
{
    echo "container=$name"
    echo "url=$url"
    echo "mcp-url=$url/mcp"
    echo "image=$image"
} >> "$GITHUB_OUTPUT"
success=true
