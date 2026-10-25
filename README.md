# RPFM Server in Docker

Run the headless RPFM backend in GitHub Actions with MCP and WebSocket access.
Release images are built once, tested, and stored in GitHub Container Registry.
Consumer jobs download the verified image instead of compiling Rust.

## GitHub Actions

```yaml
jobs:
  mod-check:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v7
      - uses: Warhammer-Mods/rpfm-server-docker@v5.3.1-server.2
        id: rpfm
      - name: Run your pack script
        env:
          RPFM_MCP_URL: ${{ steps.rpfm.outputs.mcp-url }}
        run: python3 scripts/build_mod.py
      - name: Stop RPFM
        if: always() && steps.rpfm.outputs.container != ''
        env:
          RPFM_CONTAINER: ${{ steps.rpfm.outputs.container }}
        run: docker rm --force "$RPFM_CONTAINER"
```

`scripts/build_mod.py` is your own MCP client script. See the bundled
[stdlib example](server/smoke_test.py). For a full source pin, replace the
action tag with its commit SHA.

The default image is resolved from the selected release's `rpfm-image.json`
asset, checked against the bundled RPFM/schema pins and build inputs, then
pulled and run by its exact OCI digest. No registry login is needed for the
public image. Missing release metadata fails with a clear error; it never
silently starts an expensive source build.

| Input | Default | Purpose |
| --- | --- | --- |
| `image` | Release image digest | Override with a registry reference or a local image. |
| `build-from-source` | `false` | Explicitly compile the bundled Dockerfile. Mutually exclusive with `image`. |
| `port` | `45127` | Localhost port, from 1024 to 65535. |

Outputs: `url`, `mcp-url`, `container`, and the actual `image` reference.
The job workspace is mounted at `/work`. Remove the container in an
`always()` cleanup step. Published images support Linux x86-64.

## Docker Compose

```sh
cd server
docker compose pull
docker compose up -d
```

For a local source build instead:

```sh
docker compose -f compose.yaml -f compose.build.yaml up -d --build
```

See the [server guide](server/README.md) for MCP API v2, paths, persistent
schemas, custom images and source builds. The [release guide](docs/RELEASING.md)
explains GHCR publication, SBOM, provenance, signed attestations and recovery.

## Compatibility

The root action and `/server` action both start RPFM Server. RPFM 5.3 uses
server API v2; scripts written for 5.2 must migrate tool names and results.
The desktop GUI and former CLI arguments are not provided. The historical
CLI action remains at `/legacy`; old tags retain their original behavior.
The root Dockerfile is historical; the current backend is built from `server/`.

All credit for RPFM goes to [Frodo45127](https://github.com/Frodo45127/rpfm)
and [RPFM's contributors](https://github.com/Frodo45127/rpfm/graphs/contributors).
