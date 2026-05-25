<!--
  SPDX-License-Identifier: Apache-2.0
  Copyright 2026 Fabio Marcello Salvadori
-->

# Branch protection — operational notes

This document is the audit trail for the `main` branch's protection ruleset.
It records the **current** required status checks, the exact commands used
to manage them, and the rollback procedure. If you change the ruleset,
update this file in the same PR.

> **Rule of thumb:** required-check context strings must match **exactly**
> what GitHub shows in a PR's Checks panel — case, parentheses, spaces, all
> of it. The Checks panel is the source of truth. Anything else (a workflow
> file's `name:` directive, `gh run view --json jobs`, your memory) is a
> hint, not authoritative.

---

## Current required status checks (as of v0.3.3 release hygiene)

Branch ruleset name: **Main Protection**
Target: `refs/heads/main`
Enforcement: **active**

Required checks bound in `rules[].parameters.required_status_checks[].context`:

| Context (exact string) | Emitted by |
|---|---|
| `DCO` | DCO GitHub App |
| `validate (20)` | `.github/workflows/validate.yml` — `validate` job, matrix `node-version: 20` |
| `validate (22)` | `.github/workflows/validate.yml` — `validate` job, matrix `node-version: 22` |
| `wasm-digest-check` | `.github/workflows/validate.yml` — `wasm-digest-check` job |
| `fmt` | `.github/workflows/rust.yml` — `fmt` job |
| `clippy` | `.github/workflows/rust.yml` — `clippy` job |
| `test (ubuntu-24.04)` | `.github/workflows/rust.yml` — `test` job, matrix `os: ubuntu-24.04` |
| `test (macos-14)` | `.github/workflows/rust.yml` — `test` job, matrix `os: macos-14` |
| `test (windows-2022)` | `.github/workflows/rust.yml` — `test` job, matrix `os: windows-2022` |
| `wasm-build` | `.github/workflows/rust.yml` — `wasm-build` job |

Other rules on the same ruleset:
- `non_fast_forward` (force-push blocked)
- `deletion` (branch-delete blocked)
- `pull_request` (PR required before merge; reviewing-approvals count configured via UI)

Repository setting (not part of the ruleset, but related):
- **Settings → Pull Requests → Require contributors to sign off on web-based commits**: **ON**.
  This forces a DCO-conformant signoff trailer on any commit created via the
  GitHub web UI editor. The DCO check still has to pass on CLI commits separately.

---

## Why context strings are bare job names

GitHub Actions matches required status checks by the **check_run.name** field,
which equals the job's matrix-expanded name with no workflow prefix:

- `fmt`, not `Rust / fmt`
- `test (ubuntu-24.04)`, not `Rust / test (ubuntu-24.04)`

Workflow `name:` directives affect display only (the heading shown in the
Checks panel), not matching. **Two workflows with the same job name would
both satisfy the same context** — keep job names unique across workflows.

If you ever rename a workflow `name:` field or a job key, the bound contexts
in this ruleset do **not** auto-update. PRs will silently block forever
waiting for the old name. Always update the ruleset in the same PR that
renames jobs.

---

## Discovery: getting the current state

> **Shell requirement:** these commands use bash syntax (`if`, heredocs,
> `printf`, `jq`). Run them under Linux/macOS shell, Git Bash on Windows,
> or WSL. Not PowerShell-compatible without translation.

### 1. Find the ruleset ID

IDs are not portable across repos. Resolve by listing active branch-targeted
rulesets and picking the one for `main`:

```bash
OWNER=madeinplutofabio
REPO=neural-computation-protocol

gh api repos/$OWNER/$REPO/rulesets \
  --jq '.[] | select(.target == "branch" and .enforcement == "active") | {id, name, enforcement}'
# Output should include "Main Protection" with its current id.
# Paste that id below:
RULESET_ID=<paste id>
```

### 2. Snapshot the current ruleset (always do this first)

```bash
gh api repos/$OWNER/$REPO/rulesets/$RULESET_ID > ruleset-before.json
```

Keep `ruleset-before.json` on disk through the entire edit; it's your panic
button. If anything goes wrong, restore it with the rollback command below.

### 3. Read the current required contexts

```bash
gh api repos/$OWNER/$REPO/rulesets/$RULESET_ID \
  --jq '.rules[] | select(.type=="required_status_checks") | .parameters.required_status_checks[]'
```

Use this output as the source of truth for "what's currently bound" — the
table at the top of this file should match.

---

## Adding new required checks (idempotent)

When a new workflow lands and you want to gate `main` on it:

1. **Get the exact context strings from a real PR's Checks panel.** Open
   any PR that has the new workflow running, scroll to the Checks list at
   the bottom, copy the names character-for-character. Do not retype from
   memory.

2. Build the new entries as valid JSON via a literal-quoted heredoc — the
   single quotes around `'JSON'` prevent shell from interpolating `$`,
   backticks, or other special chars in context strings:

   ```bash
   NEW_ENTRIES_JSON=$(cat <<'JSON'
   [
     {"context":"name-from-PR-checks-panel"},
     {"context":"another-name-from-PR-checks-panel"}
   ]
   JSON
   )
   ```

3. Merge + deduplicate via `--slurpfile` (writes JSON to a file rather than
   passing through shell — avoids quoting risk; `unique_by(.context)` makes
   the operation idempotent so you can re-run safely):

   ```bash
   printf '%s\n' "$NEW_ENTRIES_JSON" > new_entries.json

   jq --slurpfile new new_entries.json '
     (.rules[] | select(.type == "required_status_checks") | .parameters.required_status_checks) |=
       ((. + $new[0]) | unique_by(.context))
   ' ruleset-before.json > ruleset-after.json
   ```

4. PUT the updated ruleset back:

   ```bash
   gh api -X PUT repos/$OWNER/$REPO/rulesets/$RULESET_ID --input ruleset-after.json
   ```

5. Verify by opening any new PR (a no-op README edit is fine) and confirming
   the Checks panel shows all expected required checks gating merge.

6. Update the table at the top of this file in the same PR that makes the
   ruleset edit.

---

## Rollback (the panic button)

If a PUT goes wrong — typo in a context string, schema rejection, PRs
mysteriously blocked — restore the previous state immediately:

```bash
gh api -X PUT repos/$OWNER/$REPO/rulesets/$RULESET_ID --input ruleset-before.json
```

This is also why you keep `ruleset-before.json` on disk. **Don't try to
diagnose first; restore first, diagnose after.** A locked `main` is worse
than a temporarily-unenforced ruleset.

---

## Schema fallback (rare, future-proofing)

This repo writes required check entries as the minimal `{context: "..."}`
shape — no `integration_id` field. That's the safest shape and works
against today's API.

If a future API schema bump rejects context-only entries with a schema
error on PUT, the fallback is to **mirror an existing entry's exact
shape**: read any current entry from `ruleset-before.json`, copy all its
keys (e.g. `{context, integration_id}`), and apply the same shape to the
new entries. Don't hardcode `integration_id` values — extract them from
an existing entry that's known to work.

---

## Web-based sign-off setting

GitHub has a repository setting that auto-signs-off commits created
through the web editor (so a web-edit doesn't bypass the DCO gate):

- Repo **Settings → Pull Requests → Require contributors to sign off on
  web-based commits** → **ON**.

This is not part of the ruleset and cannot be set via `gh api` for repo
settings (only for organization-level policies). Toggle manually in the
UI. As of v0.3.3 it is enabled; verify periodically.

---

## Bypass actors

The ruleset has a bypass list containing the repo's admin role. This is
intentional for the current sole-maintainer phase: it lets the maintainer
self-merge a PR by clicking "Bypass rules and merge" rather than dropping
the `pull_request` rule to `required_approvals: 0` repo-wide.

When additional maintainers come on board, revisit:
- Required approving review count (currently 1, satisfied via bypass for
  solo work)
- Whether to keep admin bypass or remove it once a second reviewer exists
