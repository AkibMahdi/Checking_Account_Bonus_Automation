# Workload identity federation for `update-offers.yml`

`update-offers.yml` calls `scripts/extract.py`, which calls the Anthropic API. Instead
of storing a static `ANTHROPIC_API_KEY` as a GitHub secret, that job authenticates via
**workload identity federation (WIF)**: GitHub mints it a short-lived OIDC token, and
Anthropic exchanges that token for API access scoped to a specific service account and
workspace. No long-lived credential exists anywhere in this repo or its GitHub secrets.

## How it works, end to end

1. The job has `permissions: id-token: write`, which lets it ask GitHub for an OIDC
   token.
2. The **"Fetch GitHub OIDC token for Anthropic"** step (`actions/github-script@v8`)
   calls `core.getIDToken('https://api.anthropic.com')` and exports the result as the
   `JWT` environment variable (and masks it in logs via `core.setSecret`).
3. `scripts/extract.py`'s `build_client()` sees `ANTHROPIC_FEDERATION_RULE_ID` is set,
   builds an `anthropic.WorkloadIdentityCredentials` object pointed at that rule, org,
   service account and workspace, and hands it to `anthropic.Anthropic(credentials=...)`.
4. On the first API call, the SDK POSTs the JWT to Anthropic's token endpoint
   (`grant_type: urn:ietf:params:oauth:grant-type:jwt-bearer`), gets back a short-lived
   Anthropic access token, and caches it for the rest of the run.

## The four values in the workflow

```yaml
ANTHROPIC_FEDERATION_RULE_ID: fdrl_01LZgkJxBq742pMAyTd7aVy3
ANTHROPIC_ORG_ID:             a8b80901-cce8-4958-aca5-b19ef2586a9c
ANTHROPIC_SERVICE_ACCOUNT_ID: svac_01Q6XzdpxgP7nrSY3HqFWeES
ANTHROPIC_WORKSPACE_ID:       wrkspc_017yoHHCy7QsSiMKFVSJZqdP
```

These identify *configuration*, not a credential — on their own they let no one call
the API. A working token exchange also requires a GitHub OIDC JWT whose claims match
this specific rule's match conditions (repo `AkibMahdi/Checking_Account_Bonus_Automation`,
ref `refs/heads/main`), which only GitHub itself can mint for a workflow run in this repo.
That's why they're committed in plain sight in `update-offers.yml` rather than stored as
secrets.

Configured in Claude Console under **Settings → Workload identity**, GitHub Actions
issuer, rule name `github-workloads`.

## Rotating or revoking

Delete the federation rule (or the whole issuer) in Claude Console — that instantly
kills every token this workflow can mint, no repo change needed to cut off access. To
re-enable, create a new rule with the same match conditions and swap the four values
above for the new IDs.

## Troubleshooting

- **401 on token exchange**: almost always a match-condition mismatch. Check the
  auth-event log on the Workload Identity page in Console — it shows the actual claims
  GitHub's JWT presented versus what the rule expects. The usual culprit is the `ref`
  claim (a manual `workflow_dispatch` from a branch other than `main` won't match a
  rule pinned to `refs/heads/main`).
- **Token exchange works but then fails partway through a long run**: the Anthropic
  access token's lifetime is capped by the rule's "Token lifetime" setting (10 minutes
  by default) and by the underlying GitHub JWT's own expiry. `--refresh-all` across
  many URLs plus a full discovery pass can occasionally outrun a short-lived token —
  if this happens repeatedly, raise the rule's token lifetime in Console, shard the
  job into smaller `--limit` batches, or raise `--workers` (default 6 — see
  "Automation" in the README) so the same batch finishes in less wall-clock time in
  the first place. `_github_actions_jwt()` re-reads `$JWT` on every LLM call rather
  than caching it, so a token refreshed mid-run by a longer-lived rule is picked up
  automatically without a code change.
- **Want to go back to a plain API key instead**: delete the four `ANTHROPIC_*ID`
  lines from `update-offers.yml`'s job-level `env:`, add
  `ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}` to the two extract steps, and
  set that secret in Settings → Secrets and variables → Actions.
  `scripts/extract.py`'s `build_client()` falls back to a plain `anthropic.Anthropic()`
  automatically whenever `ANTHROPIC_FEDERATION_RULE_ID` isn't set — no code change
  needed either way.

## Scope

This only covers `update-offers.yml`'s calls into `scripts/extract.py` — the one place
in this repo that calls the Anthropic API. It has nothing to do with the separate
scheduled task that keeps the hosted Bonus Ladder Artifact page up to date; that runs
inside its own Cowork session with its own API access and never touches this repo's
credentials at all.
