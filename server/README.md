# RPFM Server

A headless Docker backend for Total War pack operations over MCP and
WebSocket. This image contains RPFM Server, not the Qt desktop UI or the
removed `rpfm_cli` interface. The root Marketplace action and this directory's
`/server` action share one launcher.

## Download and start

Use Docker with Linux containers. From this directory:

```sh
docker compose pull
docker compose up -d
curl http://127.0.0.1:45127/version
```

On Windows PowerShell, use `curl.exe`. The default Compose file downloads the
release image from GHCR. Set `RPFM_IMAGE` to an `image@sha256:...` reference
from the release lock asset if you want the same immutable reference as the
action. Compose otherwise uses the versioned tag, which the publisher never
overwrites.

Put packs in `work/`; `work/example.pack` on the host is `/work/example.pack`
inside RPFM. The image runs as UID/GID 1000; ensure that user can write to the
host directory. Configuration and seeded schemas persist in the `rpfm-config`
volume. Ports are bound to host localhost only.

## GitHub Actions

```yaml
- uses: Warhammer-Mods/rpfm-server-docker/server@v5.3.3-server.1
  id: rpfm
```

The root action supports the same inputs and outputs. By default, the action
downloads `rpfm-image.json` from its configured GitHub release. It validates
the release, backend version, schema/source pins, target platform and build
input fingerprint, then pulls and runs the exact digest. The helper uses
Python's standard library; consumer jobs need only Docker, Python 3 and curl,
as provided by GitHub-hosted Ubuntu runners. The release asset and GHCR
package must be public. Public consumers do not need tokens.

The job checkout is mounted at `/work`. RPFM runs as the runner's UID/GID so
saved packs can be used in later steps. Outputs are `url`, `mcp-url`,
`container`, and `image`. Always stop the returned container:

```yaml
- name: Stop RPFM
  if: always() && steps.rpfm.outputs.container != ''
  env:
    RPFM_CONTAINER: ${{ steps.rpfm.outputs.container }}
  run: docker rm --force "$RPFM_CONTAINER"
```

### Custom images and development refs

Set `image` to a local image or a registry reference. A missing local image is
pulled. A local image already loaded by a build step is reused. For immutable
custom images, pass a digest instead of a mutable tag.

Set `build-from-source: 'true'` to compile the bundled Dockerfile explicitly.
Do not also set `image`. Use this mode for unpublished development refs and
upstream candidate CI. An unavailable or mismatched release image is an error,
not a trigger for an implicit source build. Existing action tags before this
cloud-image change retain their old source-build defaults.

## Connect

* MCP: `http://127.0.0.1:45127/mcp`
* WebSocket: `ws://127.0.0.1:45127/ws`
* Version: `http://127.0.0.1:45127/version`

RPFM 5.3 uses server API v2. Call `set_game` with
`{"game":"warhammer_3","rebuild_dependencies":false}`, then `open_pack` with
`{"paths":["/work/example.pack"]}`. Tools return `structuredContent`. Use the
returned pack `key` as the `pack` argument. `save_pack` accepts an optional
`path`. `session_status` lists open packs and schema status.

Slow tools return job states. If queued or running, call `wait_for_job` with
the `job` ID until finished, then read `result`. Inspect `tools/list` for the
input/output schemas. Scripts written for 5.2 must migrate their old names and
response wrappers. See the [upstream MCP reference](https://github.com/Frodo45127/rpfm/blob/v5.3.1/docs/server/mcp.md).

All MCP clients share the selected game and open packs. WebSocket sessions
remain separate. Close only your own packs when sharing a server. An idle
MCP session is removed after five minutes only if it has no open packs or
unfinished jobs. This container does not automatically connect to ChatGPT.

For vanilla dependency diagnostics, mount game data read-only and configure
container paths in `/config/rpfm/settings.json` before starting the server,
or configure a WebSocket session with `session.configure`. Game files and
Assembly Kit files are not included. Ordinary pack operations do not require
a full game installation.

## Source builds

```sh
docker compose -f compose.yaml -f compose.build.yaml up -d --build
```

The builder compiles Rust from the exact source and schema commits in
`upstream.json`. Cargo uses `--locked`; BuildKit caches Cargo downloads and
build products. The minimal Docker context includes only the Dockerfile and
entrypoint. Image lock fingerprints also include the pins and ignore rules.
Rolling base images and OS packages mean a later rebuild can have a different
digest; a digest pin identifies a published build, not a guarantee of bit-for-bit
reproduction of a future build.

The listener binds to `127.0.0.1:45127` inside the container. A `socat` proxy
exposes container port 45128. `tini` and the entrypoint supervise both processes.
Compose restarts the service if either exits. The image contains no browser
interface or remote desktop.

## Updates and publication

The upstream watcher runs twice daily at 05:23 and 17:23 UTC. It resolves the
latest stable release tag, rejects downgrades and prereleases, and prepares
source/schema pins, the cloud image release, Compose and README examples.
It builds and tests before opening an update PR. It does not merge or publish.
A new backend version starts at server revision 1; changed pins within one
backend version increment the server revision because an existing image lock
must never be reused for different build inputs.

`Publish verified RPFM image` handles released tags and manual preparation of
a draft release. It publishes an OCI image with SBOM and build provenance,
requires an anonymous download, tests the downloaded digest through the action,
creates a GitHub OIDC attestation, then promotes the version tag and attaches
`rpfm-image.json`. See [release instructions](../docs/RELEASING.md).

Initial schema seeding never replaces an existing configuration volume's
checkout. Use the schema update tool when you need newer definitions.
`docker compose down` keeps configuration; `docker compose down -v` removes it,
including caches, schema updates and autosaves.

## Validation

```sh
python3 -m unittest discover -s server/tests -v
python3 server/smoke_test.py --roundtrip
```

Run these commands from the repository root, against an isolated instance;
it selects WH3 and creates/closes its own pack. `--roundtrip` expects the host
working directory mounted at `/work`, as in the action, and checks that a
saved pack is readable on the host and can be reopened by RPFM.

CI covers digest validation, refusal of mismatched metadata, explicit source
builds, default downloads without compilation, failure cleanup, upstream
updates and live Docker/MCP/schema/pack operations. The publisher separately
checks anonymous GHCR access and the downloaded release image. Inspect the
chosen ref's workflow results for validation of that revision.

RPFM is created by [Frodo45127](https://github.com/Frodo45127/rpfm) and its
contributors under the MIT license. See the repository LICENSE.
