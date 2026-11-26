---
name: liftover
description: Move a codebase's feature flags to Flagsmith from Unleash, LaunchDarkly or environment variables, with the Flagsmith CLI. Takes stock of every flag in the source system and the code, rebuilds them in Flagsmith (dry run first), proves every sampled user gets the same result before and after, then rewrites the SDK calls and opens the PR. Use when asked to migrate, move or switch feature flags to Flagsmith.
---

# Liftover: move feature flags to Flagsmith

A migration is done when the code reads Flagsmith and no user's experience changed without someone deciding it should.
Work in five steps. Never skip the dry run or the parity check, and never write to a production environment
without the user typing yes to the exact change list.

Scripts live in `scripts/` next to this file. They print one line per flag; read those lines, not the JSON,
unless a line needs a closer look. All state goes in `.liftover/` in the repo being migrated.

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

## 2. Plan  (next: plan.py)
## 3. Apply, dry run first  (next: apply.py)
## 4. Parity check  (next: parity.py)
## 5. Code cutover and PR
