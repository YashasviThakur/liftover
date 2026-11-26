---
name: liftover
description: Move a codebase's feature flags to Flagsmith from LaunchDarkly, Unleash or homegrown environment-variable flags, with the Flagsmith CLI. Takes stock of every flag in the source system and the code, rebuilds them in Flagsmith (dry run first, production only after the user approves the exact change list), proves sampled users get the same flags from both systems with each system's own SDK, then rewrites the flag reads and opens the PR. Use when asked to migrate, move, import or switch feature flags to Flagsmith, or to leave LaunchDarkly or Unleash.
license: MIT
compatibility: Needs Python 3.10+, the Flagsmith CLI v2, git and gh; step 4 needs pip packages flagsmith plus UnleashClient or launchdarkly-server-sdk. Works with Flagsmith Cloud and self-hosted.
metadata:
  version: "1.1"
  source-systems: "launchdarkly, unleash, env-vars"
---

# Liftover: move feature flags to Flagsmith

Done means the code reads Flagsmith and no user's experience changed unless someone decided it should. Five steps.
Never skip the dry run or the parity check; never write to production without the user typing yes to the exact change list.

Run scripts from the repo being migrated, with the Python that has the SDK packages (call it by full path).
Each prints one line per flag: read those, not the JSON. State goes in `.liftover/` (git-ignore it). How a rule is
translated: [references/mapping.md](references/mapping.md). Flagsmith access comes from `FLAGSMITH_API_URL`, an admin
key (`FLAGSMITH_API_KEY` or `FLAGSMITH_API_KEY_<host>_<port>`) and `FLAGSMITH_ORGANISATION`. Never print keys.

## 1. Inventory
`python scripts/inventory.py --repo . [--unleash] [--launchdarkly production=ld-prod.json,...] [--env-files .env.production]`

Read-only. Unleash: `UNLEASH_URL` (ending `/api`) + `UNLEASH_ADMIN_TOKEN`. LaunchDarkly: per environment,
`curl -sH "Authorization: $LD_SDK_KEY" https://sdk.launchdarkly.com/sdk/latest-all > ld-prod.json`.
Show the table and settle every `review` row with the user: read in code but missing from the source -> leave out and
say so; not read anywhere -> default retire; config value -> migrate as remote config only if they agree.
An env-var flag showing `-` has no value in the given env files: it migrates as off, and the plan says so.

## 2. Plan
`python scripts/plan.py --retire k1,k2 --skip k3 --config k4 [--migrate k5] [--env-map production=Production]`

Offline. Each step-1 decision is one option: `--retire` (removed from code in step 5), `--skip` (left alone),
`--config` (remote-config value), `--migrate` (despite the note). Exit 2 with `UNDECIDED` lines: settle them, rerun.
`cannot map automatically` (prerequisites, dates, source-side segments) is never guessed: the user rebuilds it by hand
or keeps it in the source, then `--skip` it. Repeat every `warning`. Tell the user percentage rollouts keep their share
of users but not the same users.

## 3. Apply, dry run first
```
python scripts/apply.py --project <name>                                      # dry run: only what differs, PROD marked
python scripts/apply.py --project <name> --execute                            # non-production written, production held
python scripts/apply.py --project <name> --execute --confirm-production <token>
```
Show the user every `PROD` line. Only after they answer yes, rerun with the token printed by the dry run; if anything
changed meanwhile the token no longer matches: dry-run again. Reruns are safe and repair hand edits. Everything created
is tagged `migrated` and recorded for `--rollback` (dry run) / `--rollback --execute`; roll back only before the cutover ships.

## 4. Parity
`python scripts/parity.py --project <name> [--users 4000]`

Samples users, evaluates each flag in the source and in Flagsmith with their own SDKs. `exact` flags must match for
every user: a `FAIL` lists example users, so fix the plan or rule, re-apply, rerun, never move on with a failure.
`statistical` flags (rollouts, variants) pass within tolerance; tell the user how many users switch bucket.
Spot-check: `flagsmith eval <flag> --identity <id> --trait plan=pro -e <env key> --jq .value`.

## 5. Cutover
`python scripts/cutover.py --repo .` (worklist), then `--check` until it passes (writes `.liftover/pr.md`).

Edit only the listed lines and the client setup: one Flagsmith client whose `default_flag_handler` returns off (the
Python SDK raises without it); old context attributes become traits with their types kept (numbers stay numbers);
an env-var flag replaces the whole comparison; a value flag keeps the old default for `None`; retired flags lose the check
and keep today's branch. A `STOP` line is still served by the source: ask first. Commit on a branch, then
`gh pr create --title "Move feature flags to Flagsmith" --body-file .liftover/pr.md`. Keep the old system as the fallback.
