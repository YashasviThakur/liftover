---
name: liftover
description: Move a codebase's feature flags to Flagsmith from Unleash or homegrown environment-variable flags, with the Flagsmith CLI. Takes stock of every flag in the source system and the code, rebuilds them in Flagsmith (dry run first, production only after the user approves the exact change list), proves sampled users get the same flags from both systems, then rewrites the flag reads and opens the PR. Use when asked to migrate, move or switch feature flags to Flagsmith.
---

# Liftover: move feature flags to Flagsmith

A migration is done when the code reads Flagsmith and no user's experience changed without someone deciding it should.
Work in five steps. Never skip the dry run or the parity check, and never write to a production environment
without the user typing yes to the exact change list.

Scripts live in `scripts/` next to this file. Run them from the root of the repo being migrated. They print one line
per flag; read those lines, not the JSON, unless a line needs a closer look. All state goes in `.liftover/` there
(add it to `.gitignore`).

Setup, once: Python 3.10+, the Flagsmith CLI v2 (`npm install -g @flagsmith/cli`), and for step 4
`pip install flagsmith UnleashClient`. Flagsmith access comes from the environment: `FLAGSMITH_API_URL`, an admin
API key (`FLAGSMITH_API_KEY`, or `FLAGSMITH_API_KEY_<host>_<port>` for self-hosted) and `FLAGSMITH_ORGANISATION`.
Never print or commit keys.

## 1. Inventory

```
python scripts/inventory.py --repo <repo> [--unleash] [--env-files .env.production,.env.staging]
```

Needs `UNLEASH_URL` (ending `/api`) and `UNLEASH_ADMIN_TOKEN` for `--unleash`. Read-only against the source.

Show the user the table. Settle every row with a `review` note before moving on:
- **read in code but missing from the source system**: the code always gets the default today. Usually leave it out
  of the migration and tell the user.
- **not read anywhere in the code**: ask whether to migrate it or retire it. Default to retiring.
- **config value, not obviously a flag**: migrate as a Flagsmith remote-config value only if the user agrees.
- For environment-variable flags, the `code_refs` line shows how the value is compared (`== "true"`, `== "1"`).
  That comparison, not the raw value, decides whether the flag is on.

## 2. Plan

```
python scripts/plan.py --retire <keys> --skip <keys> --config <keys> [--migrate <keys>] [--env-map production=Production]
```

Offline: reads `.liftover/inventory.json`, writes `.liftover/plan.json`. Pass each step-1 decision as one option:
`--retire` (not migrated, removed from code in step 5), `--skip` (not migrated, code left alone), `--config`
(remote-config value), `--migrate` (migrate despite the note). Exit 2 with `UNDECIDED` lines means the plan is
incomplete and step 3 will refuse it: settle each line with the user and rerun.

How rules map, so you can explain the table:
- Unleash strategies are OR-ed, constraints inside one are AND-ed. Each targeted flag+environment becomes one segment
  `<flag>_<env>`: root `ANY`, one `ALL` child per strategy. The environment default is off; the segment turns it on.
- `userId` becomes `$.identity.identifier`. A rollout % becomes `PERCENTAGE_SPLIT`: the same share of users, but not
  the same users, because the hash differs. Say this to the user before step 3.
- `cannot map automatically` (dates, Unleash segments, session stickiness): rebuild that flag by hand or leave it in
  the source, then `--skip` it. `warning` lines are not blocking, but repeat them to the user.

Show the user the printed table and get agreement before step 3.

## 3. Apply, dry run first

```
python scripts/apply.py --project <Flagsmith project>                     # dry run, always first
python scripts/apply.py --project <name> --execute                        # non-production changes only
python scripts/apply.py --project <name> --execute --confirm-production <token>
```

The dry run reads Flagsmith and prints only what differs from the plan, one line per change; `PROD` marks changes to
a production environment (name contains `prod`, or `--production-envs`). It ends with a token for exactly that list.
1. Run the dry run and show the user every `PROD` line.
2. Run `--execute` without the token: development and staging get written, production is held.
3. Only after the user replies yes to those lines, rerun with `--confirm-production <token>`. If anything changed in
   between, the token no longer matches and the script refuses: dry-run again and show the new list.

Reruns are safe: it continues where a failed run stopped, and reports `nothing to do` when Flagsmith matches the plan.
If someone edits a migrated segment by hand, the next dry run lists the fix. Everything created is tagged `migrated`
and recorded in `.liftover/applied.json`; `--rollback` (dry run) and `--rollback --execute` delete it, the whole
project if liftover created it. Roll back only before the code cutover ships; afterwards, revert the PR first.

## 4. Parity check

```
python scripts/parity.py --project <name> [--users 4000]
```

Evaluates sampled users offline in both systems with their own SDKs: Unleash from a client-token bootstrap
(`UNLEASH_URL`, `UNLEASH_CLIENT_TOKEN_<ENV>` per environment), Flagsmith from `flagsmith environment document`.
The users are built to hit both sides of every rule and to miss each trait sometimes.
- `exact` flags must match for every user. A `FAIL` line lists example users and traits: fix the plan or the
  Flagsmith rule, re-apply, and rerun. Never move on with a failure.
- `statistical` flags (rollouts, variants) pass when each share is within the tolerance. Tell the user how many users
  switch bucket: it is expected, and they should know before shipping.

It writes `.liftover/parity.md`, which becomes part of the PR body.

## 5. Code cutover and PR

```
python scripts/cutover.py --repo .            # the worklist
python scripts/cutover.py --repo . --check    # repeat until it passes; writes .liftover/pr.md
```

The worklist gives every line to change and its Flagsmith replacement. Edit only those lines and the client setup.
- Build one Flagsmith client with `default_flag_handler` returning off. The old SDKs answer off for unknown flags, and
  the Python SDK raises without it.
- Pass the same attributes the old context had as traits. Keep their types: numbers as numbers, or numeric rules stop
  matching.
- For an environment-variable flag, the Flagsmith flag already holds the result of the old comparison, so replace the
  whole comparison. A config value keeps the code's old default for when the value is unset.
- Retired flags: delete the check and keep the branch users get today.

When `--check` passes, commit on a new branch and open the PR with
`gh pr create --title "Move feature flags to Flagsmith" --body-file .liftover/pr.md`. Do not remove the old system
in the same change; it stays as the fallback until the PR has been live for a while.
