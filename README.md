# RPFM Server in Docker

Start **RPFM Server** in GitHub Actions for Total War packfile operations over
Streamable HTTP MCP or WebSocket. The action runs on Linux runners with Docker,
mounts your checkout at `/work`, and builds the pinned backend unless you supply
a prebuilt image.

## GitHub Actions

```yaml
jobs:
  mod-check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: Warhammer-Mods/rpfm-server-docker@v5.3.0-server.1
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

The example uses a published release tag. Pin a full commit SHA to lock the
action source.
The bundled RPFM version and source pins are recorded in [server/upstream.json](server/upstream.json).
`scripts/build_mod.py` is your own MCP client script; it is not included.
See [the smoke test](server/smoke_test.py) for a Python standard-library MCP
client example.

Inputs: `image` (optional prebuilt Docker image) and `port` (default `45127`).
Outputs: `url`, `mcp-url` and `container`. Remove the container in an `always()`
cleanup step. The desktop GUI and former CLI arguments are not provided.

See [the server guide](server/README.md) for Docker Compose, schemas, pack paths,
client usage, CI validation and automatic stable-release builds. New releases
are checked twice daily, built and tested before an update PR is opened.

## Repository rename

The repository is now `Warhammer-Mods/rpfm-server-docker`. Update existing
workflow `uses:` references from `Warhammer-Mods/rpfm_cli-docker` to the new
name, keeping the same subdirectory and tag or commit. GitHub Actions does
not follow repository-name redirects. The historical CLI image remains at
`ghcr.io/warhammer-mods/rpfm_cli-docker:develop`.

## Migration from the legacy CLI

The root action now starts RPFM Server. Replace the former `game`, `options`,
`subcommand` and `run` inputs with a script using MCP or WebSocket.
The `/server` action remains available with the same server interface.
The historical CLI action is preserved at `/legacy`; older tags and pinned
commits keep their original behavior. Its existing container image is unchanged.
The root Dockerfile is also historical; the current server Dockerfile is under
`server/`.

All credit for RPFM goes to [Frodo45127](https://github.com/Frodo45127/rpfm) and
[RPFM's contributors](https://github.com/Frodo45127/rpfm/graphs/contributors).
