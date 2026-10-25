# Releasing a verified cloud image

The canonical image is `ghcr.io/warhammer-mods/rpfm-server-docker`.
Each action release has one image tag, without the leading `v`, and a
`rpfm-image.json` release asset containing the tested OCI digest. Consumers
use the digest, not a floating `latest`, major or minor tag.

## First-time setup

1. Merge the cloud-image workflows and launcher before creating the first
   cloud action release. The initial revision is `v5.3.1-server.2`.
2. Allow the repository's Actions token to publish its GHCR package. The
   workflow uses `GITHUB_TOKEN` with `packages: write`; no PAT is required.
3. GHCR initially creates packages as private even for public source
   repositories. After the first candidate upload, open the package settings
   and change visibility to **Public**, then rerun the workflow. The anonymous
   pull step intentionally prevents promotion until public access works.
4. If an existing package has that name, connect it to this repository and
   grant its workflows write access before publishing.

The publisher runs only in the canonical repository, never on PR events.
The release tag must match `server/image.json` and point to a commit already
in `master`. Third-party actions in the publisher are pinned by full SHA.

## Prepare, validate, then publish

1. Merge the version PR after CI succeeds. Check `server/upstream.json`,
   `server/image.json`, Compose and both README examples together. For image
   changes without a backend version change, increment the server revision.
2. Create and push an annotated tag on that merged commit. For example:

   ```sh
   git tag -a v5.3.1-server.2 <merged-commit-sha> -m 'RPFM Server 5.3.1: verified GHCR images'
   git push origin refs/tags/v5.3.1-server.2
   ```

3. Create a **draft** GitHub release for the existing tag. Copy the prepared
   [release notes](releases/v5.3.1-server.2.md), select Latest and enable
   **Publish this Action to the GitHub Marketplace**.
4. Run **Publish verified RPFM image** manually from `master`, passing the tag:

   ```sh
   gh workflow run server-publish.yml --ref master -f tag=v5.3.1-server.2
   ```

5. Wait for the workflow to pass. It builds with SBOM and provenance, pushes
   a candidate, downloads it anonymously by digest, and checks startup, MCP,
   WH3 schemas and a pack disk roundtrip. Only then does it attest the image,
   promote its version tag and upload the lock asset to the draft release.
6. Inspect the run summary and `rpfm-image.json`, then publish the draft.
   Publishing triggers an idempotent verification run: a completed lock is
   validated and its digest retested instead of being rebuilt or overwritten.
7. Confirm the release appears in Marketplace. Test the action without
   `image` or `build-from-source` in a consumer repository; its `image` output
   must contain `@sha256:` and there must be no Docker build step.

A published release also triggers the same workflow automatically if the
image was not prepared manually. During that build the default action cannot
start until the lock asset is attached. Preparing a draft first avoids that
availability gap.

The release asset must remain attached and public for the default resolver.
No GitHub Actions artifact is used as a permanent distribution channel.

## Verify or use an image directly

The workflow summary supplies the actual digest. Replace the placeholder:

```sh
docker pull ghcr.io/warhammer-mods/rpfm-server-docker@sha256:<digest>
gh attestation verify oci://ghcr.io/warhammer-mods/rpfm-server-docker@sha256:<digest> --repo Warhammer-Mods/rpfm-server-docker
```

The image also includes BuildKit SBOM and provenance attestations. A digest
identifies the exact published build; rolling Rust/OS base images mean that
rebuilding later can produce a different digest.

## Recovery and maintenance

* A run failing before promotion leaves only a candidate tag. Fix the cause
  and rerun. Candidate tags are not consumed by the default action.
* If the version tag exists but the lock upload failed, the workflow refuses
  to overwrite it. Confirm the source commit, the run's tested digest and its
  attestation; regenerate the lock with `python3 server/image.py lock
  --digest sha256:<verified-digest> --output rpfm-image.json` at the release
  checkout, then attach it with `gh release upload <tag> rpfm-image.json`.
  Rerun verification afterward. Never substitute an untested digest.
* A mismatched lock, changed build context, changed pins or wrong platform
  fails closed. Fix the release metadata or publish a new server revision.
  `build-from-source: 'true'` is an explicit development escape hatch.
* Keep version tags and lock assets for published action versions. Remove
  abandoned candidates only after confirming they are not referenced by a
  release. No automatic package deletion is configured.
* This release supports `linux/amd64`. Add other architectures only together
  with native builds and live smoke checks for each supported platform.

GitHub references: [GHCR](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry),
[artifact attestations](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations),
[Marketplace publishing](https://docs.github.com/en/actions/how-tos/create-and-publish-actions/publish-in-github-marketplace).
