<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# Publishing a release

End-to-end ceremony for cutting a new NCP version. This is the maintainer-facing
companion to [`docs/INSTALL.md`](INSTALL.md) (which is adopter-facing).

> **Phase 3A.1 build-out:** this doc is added incrementally across three PRs.
> - **PR B (this PR)** — Sections 1–6: tag preparation, signed-tag creation,
>   `Release` workflow ceremony, RC test-tag flow.
> - **PR C** — Section 7: GHCR Docker image publish (added when the Docker
>   workflow lands).
> - **PR D** — Section 8: `cargo publish` to crates.io (added when the
>   runtime crate version bumps to 0.3.4).
> When all three PRs ship, this doc covers the full ceremony.

---

## 1. Prerequisites

| Need | How to verify |
|---|---|
| GPG key configured for signed tags | `git config --get user.signingkey` returns a non-empty key ID. Verify the key still works: `echo test \| gpg --clearsign` should prompt for passphrase and produce a signed block. |
| Push access to `main` + tag push permission | `gh auth status` shows the active account with `repo` scope. |
| `gh` CLI authenticated | `gh auth status` clean. |
| Zenodo integration ON for the repo | Visit https://zenodo.org/account/settings/github/ — toggle for `madeinplutofabio/neural-computation-protocol` is **enabled**. |
| Working tree clean on latest `main` | `git status -sb` shows `## main...origin/main` with no modifications. |

## 2. Confirm release state

Before tagging, the next-release version must already be on `main`:

```bash
git checkout main
git pull --ff-only origin main
grep '^version' runtime/Cargo.toml        # should match the upcoming tag (no leading v)
grep '^## \[' CHANGELOG.md | head -3       # latest entry should be the upcoming version
```

If the version still shows the previous release (e.g. `0.3.3` when you're trying
to cut `v0.3.4`), **STOP** — PR D's version-bump didn't land. Tagging now would
produce binaries that report the wrong `ncp --version`.

## 3. Dry-run the release workflow against an existing tag (optional but recommended)

The `Release` workflow accepts a `workflow_dispatch` input with `publish=false`
(default), which lets you exercise the build matrix without touching any
existing GitHub Release.

```bash
gh workflow run release.yml \
  -f tag=v0.3.3 \
  -f publish=false
```

Then check the run:

```bash
gh run list --workflow release.yml --limit 1
```

When complete, all 3 archive artifacts should be downloadable from the Actions
run page. Verify the `v0.3.3` GitHub Release page is **unchanged** (no new
assets). If `publish=false` ever produces assets on the existing Release, that's
a workflow bug — file an issue.

## 4. Push a pre-release tag (RC)

Pre-release tags (`v*-rc.*`, `v*-alpha.*`, `v*-beta.*`) are auto-marked as
GitHub `prerelease=true` by the workflow. This lets you exercise the full
release path end-to-end before committing to a real version.

```bash
TAG="v0.3.4-rc.1"
git tag -s "$TAG" -m "$TAG release candidate"
git push origin "$TAG"
```

The push fires the `Release` workflow (no dispatch needed — push to `v*` always
publishes). Watch progress:

```bash
gh run watch
```

Expected outputs once the workflow completes:

- A GitHub Release at `https://github.com/madeinplutofabio/neural-computation-protocol/releases/tag/v0.3.4-rc.1`, **marked "Pre-release"**.
- 4 assets attached: 3 archives + `SHA256SUMS`.
  - `ncp_v0.3.4-rc.1_linux_x86_64.tar.gz`
  - `ncp_v0.3.4-rc.1_macos_aarch64.tar.gz`
  - `ncp_v0.3.4-rc.1_windows_x86_64.zip`
  - `SHA256SUMS`

## 5. Verify the pre-release artifacts

```bash
mkdir -p /tmp/ncp-rc-verify && cd /tmp/ncp-rc-verify
gh release download v0.3.4-rc.1 --repo madeinplutofabio/neural-computation-protocol

# Verify checksums
shasum -a 256 -c SHA256SUMS --ignore-missing

# Extract one archive and run a quick verify
tar -xzf ncp_v0.3.4-rc.1_linux_x86_64.tar.gz   # adjust for your platform
cd ncp_v0.3.4-rc.1_linux_x86_64
./ncp --version    # should print the version (without -rc.1 — cargo's package version)
./ncp run examples/graphs/echo-pipeline/graph.yaml --input examples/graphs/echo-pipeline/sample.json
```

If anything fails: do NOT proceed to step 6. Debug the workflow, fix, retag a
new RC (`v0.3.4-rc.2`), and re-verify.

## 6. Clean up the RC tag and push the real tag

Once the RC verifies clean, delete the RC artifacts before cutting the real tag.
Leaving an RC tag pullable indefinitely confuses adopters.

```bash
RC="v0.3.4-rc.1"
gh release delete "$RC" --yes
git push origin --delete "$RC"
git tag -d "$RC"
```

> **PR C will extend this section** with `gh api`-based GHCR Docker tag cleanup
> (each release publishes two GHCR tags; both must be deleted on RC cleanup).

Then push the real tag:

```bash
TAG="v0.3.4"
git tag -s "$TAG" -m "$TAG"
git push origin "$TAG"
gh run watch    # follow the Release workflow
```

The workflow produces the same artifact set as the RC, this time on a
**non-prerelease** GitHub Release.

## 6.1 Verify Zenodo auto-archive (~3-5 min after release publishes)

Zenodo's GitHub integration archives every GitHub Release into the project's
concept DOI record. Verify:

```bash
# Concept DOI (stable, points to "latest"):
echo "https://doi.org/10.5281/zenodo.19570209"
```

Open the URL. The latest version should match the tag you just pushed. If
Zenodo doesn't update within ~10 minutes, check the integration page (see
Section 1's Prerequisites table) — the webhook may have been disabled.

---

## Sections coming in PR C / PR D

- **§7 — GHCR Docker image** (PR C) — `docker.yml` workflow + image tag cleanup.
- **§8 — `cargo publish` to crates.io** (PR D) — pre-flight `cargo publish --dry-run`, manual publish command, README link audit on crates.io render.

When all three PRs ship, this doc is the single canonical reference for the
full release ceremony.
