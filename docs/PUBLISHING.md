<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# Publishing a release

End-to-end ceremony for cutting a new NCP version. This is the maintainer-facing
companion to [`docs/INSTALL.md`](INSTALL.md) (which is adopter-facing).

> This doc covers the full release ceremony end-to-end: tag preparation,
> signed-tag creation, RC test-tag flow, GitHub Release artifacts via the
> `Release` workflow, GHCR Docker image publish via the `Docker` workflow,
> and `cargo publish` to crates.io. Built out incrementally across PRs B–D
> of Phase 3A.1.

---

## 1. Prerequisites

| Need | How to verify |
|---|---|
| GPG key configured for signed tags | `git config --get user.signingkey` returns a non-empty key ID. Verify the key still works: `echo test \| gpg --clearsign` should prompt for passphrase and produce a signed block. |
| Push access to `main` + tag push permission | `gh auth status` shows the active account with `repo` scope. |
| `gh` CLI authenticated | `gh auth status` clean. |
| GHCR cleanup permission | `gh auth status` should show package scopes before RC cleanup runs `gh api -X DELETE` against GHCR. If missing, run `gh auth refresh -s read:packages -s delete:packages`. Without this, the §6 RC cleanup script will fail with `403` even if `repo` scope is present. |
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

## 3.1 crates.io publish dry-run (pre-flight gate)

Run this **before tagging** so any crates.io-side blocker (name squat,
expired token, README-render regression, `categories` allowlist drift,
`include` array dropping `LICENSE`/`NOTICE`, or repo-relative Markdown
links that would 404 on the rendered crates.io page) surfaces while it
can still be fixed by a tiny follow-up PR — not after the release tag
is pushed and the version is already burned.

Run from `main` HEAD with the upcoming version already in
`runtime/Cargo.toml` (version-bump PR merged):

```bash
# 1. File-listing gate — proves LICENSE + NOTICE + README ship in the
#    .crate (Apache 2.0 §4(d) compliance + crates.io render). Mirrors
#    the original PR A hard gate.
cargo package -p ncp-runtime --list > /tmp/ncp-runtime-package-files.txt
grep -Fx README.md /tmp/ncp-runtime-package-files.txt
grep -Fx LICENSE /tmp/ncp-runtime-package-files.txt
grep -Fx NOTICE /tmp/ncp-runtime-package-files.txt
# Expected: all three commands exit 0.

# 2. Live dry-run against crates.io's validation API. Catches name
#    squat, expired token, allowlist drift, oversized .crate (10 MB
#    limit), and packaging metadata regressions.
cargo publish -p ncp-runtime --dry-run --locked

# 3. README-content gate — proves the README inside the .crate does not
#    contain repo-relative links that crates.io will rewrite under
#    runtime/. Catches the v0.3.4-class regression where the local
#    dry-run was green but the rendered crates.io README shipped with
#    broken links.
VERSION=$(grep '^version' runtime/Cargo.toml | sed -E 's/version = "([^"]+)"/\1/')
CRATE="target/package/ncp-runtime-${VERSION}.crate"
CHECK_DIR="/tmp/ncp-crate-render-check"

cargo package -p ncp-runtime --locked

rm -rf "$CHECK_DIR"
mkdir -p "$CHECK_DIR"
tar xzf "$CRATE" -C "$CHECK_DIR"

README="$CHECK_DIR/ncp-runtime-${VERSION}/README.md"

# Markdown links that must be absolute GitHub URLs in the published README.
# The (\./)? prefix catches both bare relative (docs/...) and dot-prefixed
# relative (./docs/...) forms, so future link drift can't slip past.
if grep -nE '\]\((\./)?(docs/|spec/|BENCHMARK\.md|COST_MODEL\.md|CONTRIBUTING\.md|SECURITY\.md|LICENSE|NOTICE)' "$README"; then
  echo "ERROR: published README still contains bare repo-relative Markdown links"
  exit 1
fi

# HTML image/link sources that must not remain repo-relative.
if grep -nE 'src="(NCP-logo\.png|docs/|spec/)' "$README"; then
  echo "ERROR: published README still contains bare repo-relative HTML src links"
  exit 1
fi

echo "README link gate passed: no bare repo-relative links found in published README"
```

All three gates must exit 0 cleanly.

**If any gate fails after the version-bump PR has merged:** the upcoming
version is now "tainted" — `runtime/Cargo.toml` claims the version but
you can't actually publish it. Recovery is a tiny follow-up PR that lands
the fix on top of the existing version (if the fix is metadata-only and
the version hasn't been tagged yet), OR bump to the next patch (e.g.
`0.3.4` → `0.3.5`) if the version slot is unrecoverable. **Either way,
do not push the release tag until §3.1 is green.**

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

> ⚠ **Before running the script below**, confirm your `gh` CLI has GHCR
> package-delete permission. GHCR package-version deletion requires the
> `delete:packages` scope (and `read:packages` to look up the version ID).
> If the `gh api -X DELETE` step returns `403`, refresh the token first:
> ```bash
> gh auth refresh -s read:packages -s delete:packages
> ```

```bash
RC="v0.3.4-rc.1"

# Delete the GitHub Release + remote+local git tag
gh release delete "$RC" --yes
git push origin --delete "$RC"
git tag -d "$RC"

# Delete BOTH GHCR image tags. docker.yml publishes the RC as `v0.3.4-rc.1`
# AND `0.3.4-rc.1` (strict-pin: two equivalent forms per release). Leaving
# either tag pullable indefinitely confuses adopters with a "ghost" RC.
#
# Two cross-platform/API quirks baked into this script:
#
# (1) --paginate is REQUIRED. GitHub paginates the package-versions
#     endpoint (~30 per page). Without it, after enough releases the
#     target tag may silently fall to page 2+ and the lookup returns
#     nothing, leaving a stale GHCR tag on the registry.
#
# (2) `gh api` paths are written WITHOUT a leading slash
#     (`users/...`, not `/users/...`). On Windows Git Bash / MSYS, a
#     leading slash gets rewritten by the shell as a filesystem path
#     (e.g. `C:/Program Files/Git/users/...`), causing `gh api` to
#     reject it with `invalid API endpoint`. The no-leading-slash form
#     is portable across Linux, macOS, and Git Bash on Windows.
OWNER=madeinplutofabio
RC_SEMVER="${RC#v}"   # v0.3.4-rc.1 -> 0.3.4-rc.1
for ghcr_tag in "$RC" "$RC_SEMVER"; do
  VERSION_ID=$(gh api --paginate "users/$OWNER/packages/container/ncp/versions" \
    --jq ".[] | select(.metadata.container.tags[]? == \"$ghcr_tag\") | .id" \
    | head -n 1)
  if [ -n "$VERSION_ID" ]; then
    echo "Deleting GHCR tag $ghcr_tag (version id $VERSION_ID)"
    gh api -X DELETE "users/$OWNER/packages/container/ncp/versions/$VERSION_ID"
  else
    echo "GHCR tag $ghcr_tag not found (already cleaned up or never created?)"
  fi
done
```

> 💡 **First-publish exception.** If the RC version is currently the only
> tagged version in the GHCR package, the GHCR delete steps above will fail
> with `HTTP 400: You cannot delete the last tagged version of a package.
> You must delete the package instead.` That's because GitHub's API refuses
> to delete the last tagged version on a package. **Don't** "fix" this by
> deleting the whole package — that's destructive. Instead: skip the GHCR
> cleanup, push the real tag (below), wait for the real-tag Docker workflow
> to publish the real-tag GHCR versions, then re-run **just the GHCR
> cleanup loop** (the `for` loop). The package now has the real-tag
> versions, so the RC version is no longer "the last tagged" and the API
> will allow deletion. Subsequent releases (every release after the first)
> don't hit this.

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

## 7. GHCR Docker image (auto-published by docker.yml)

The `Docker` workflow (`.github/workflows/docker.yml`) fires on the same
`v*` tag push that triggers the Release workflow. Both run concurrently
on a single tag push — no separate operator action required.

Each release publishes **two equivalent strict-pin tags** to GHCR:
- `ghcr.io/madeinplutofabio/ncp:v0.3.4` (matches the git tag verbatim)
- `ghcr.io/madeinplutofabio/ncp:0.3.4` (semver-canonical form)

Same image digest, two reference forms. No `:latest`, no floating `:0.3`.

### 7.1 Optional: dispatch build-only dry-run for an existing tag

The Docker workflow accepts a `workflow_dispatch` input with `push: false`
(default) so you can build the image without pushing it to GHCR — useful
for diagnosing Dockerfile/image-build issues without pushing or mutating
GHCR. Note: the dispatch checks out the resolved tag, so this only works
for tags that **already exist** in the repo (re-validating an existing
tag's build, not pre-tag-push testing).

```bash
gh workflow run docker.yml \
  -f tag=v0.3.4 \
  -f push=false
```

The image gets built on the runner, build cache populates, but the
`docker push` step is skipped. Inspect the workflow logs for any build
errors. Set `push=true` to actually publish.

### 7.2 Post-publish verification

After a real tag push (or after a dispatch with `push=true`):

```bash
# Pull both tag forms — should resolve to the same image digest
docker pull ghcr.io/madeinplutofabio/ncp:v0.3.4
docker pull ghcr.io/madeinplutofabio/ncp:0.3.4

# Confirm both refer to the same image (digest equality)
docker image inspect --format '{{.Id}}' ghcr.io/madeinplutofabio/ncp:v0.3.4
docker image inspect --format '{{.Id}}' ghcr.io/madeinplutofabio/ncp:0.3.4

# Functional verify (mirrors docs/INSTALL.md quick-verify)
docker run --rm ghcr.io/madeinplutofabio/ncp:v0.3.4 --version
docker run --rm ghcr.io/madeinplutofabio/ncp:v0.3.4 \
  run examples/graphs/echo-pipeline/graph.yaml \
  --input examples/graphs/echo-pipeline/sample.json
```

### 7.3 First-publish: GHCR package visibility (ONE-TIME setup)

The first push to a previously-non-existent GHCR namespace creates the
package as **private** by default. Adopters won't be able to `docker
pull` until visibility is flipped to public.

After the first successful `v*` tag push that runs docker.yml:

1. Visit https://github.com/madeinplutofabio?tab=packages and click the
   `ncp` container package.
2. Click **Package settings** (right sidebar).
3. Scroll to **Danger Zone → Change visibility → Public**.
4. Confirm by typing the package name.

Subsequent releases inherit the current visibility — this is a one-time
step. Verify the package is actually publicly pullable (the behavioral
test, not a registry-protocol probe — OCI registries can challenge
even public images with 401+WWW-Authenticate as part of normal auth
handshake, so HTTP status alone isn't a reliable visibility signal):

```bash
# Make sure no cached GHCR credentials are in play
docker logout ghcr.io 2>/dev/null || true

# Anonymous pull — succeeds for public, fails with "denied" for private
docker pull ghcr.io/madeinplutofabio/ncp:v0.3.4
```

---

## 8. `cargo publish` to crates.io

Final irreversible step. Runs after the GitHub Release + GHCR image are
verified clean (§5–§7). The pre-flight gate in §3.1 already established
that the publish payload is valid — §8 is the live `cargo publish`.

**Publish from the resolved tag**, not from `main` HEAD. Even one
commit's drift between tag push and `cargo publish` would ship code on
crates.io that doesn't match the v-tag's GitHub Release tarball or its
GHCR image digest:

```bash
git fetch --tags origin
git checkout v0.3.4
cargo publish -p ncp-runtime --locked
```

`--locked` is correct here — the tag checkout is clean (not "dirty"),
and `--locked` guarantees `cargo publish` resolves dependencies against
the exact `Cargo.lock` committed for the release. Do NOT pass
`--allow-dirty` from a tag checkout; if cargo complains about a dirty
tree, something is wrong (untracked files, stray edits) — investigate
before publishing.

### 8.1 Post-publish verification

Clean-host install proves an end-user `cargo install` works against the
freshly-published crate. Run on a host with no NCP source checkout
(e.g. a throwaway Docker container):

```bash
cargo install ncp-runtime --version 0.3.4 --locked
ncp --version
# Expected: prints "0.3.4"
```

Or from a clean Docker container (mirrors the environment most adopters
will hit):

```bash
docker run --rm rust:1.94-bookworm bash -c \
  "cargo install ncp-runtime --version 0.3.4 --locked && ncp --version"
```

### 8.2 README link audit on crates.io

crates.io renders the `runtime/Cargo.toml`-declared README (`../README.md`)
as the crate's landing-page docs. Relative links that work on GitHub
(e.g. `docs/ADOPTION_GUIDE.md`) may 404 on the crates.io renderer
because there is no `docs/` directory inside the `.crate` package.

1. Open https://crates.io/crates/ncp-runtime/0.3.4
2. Scroll through the rendered README.
3. Click every link.
4. If any 404: ship a tiny follow-up PR converting the broken relative
   links to absolute `https://github.com/madeinplutofabio/neural-computation-protocol/blob/main/…`
   URLs. Not a 3A.1 blocker (cosmetic), but the follow-up should land
   within 1–2 days of publish to avoid lingering broken links.

### 8.3 Scope

This phase publishes **`ncp-runtime` only**. Brick crates
(`ncp-echo`, `ncp-classifier-stub`) are `cdylib`s targeting
`wasm32-unknown-unknown` and not useful as host-target deps; they're
deferred to Phase 3A.4 (SDKs) when the brick-author workflow lands.
