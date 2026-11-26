# Liftover

An agent skill that moves a codebase's feature flags to Flagsmith, and proves nothing changed for your users.

Ask your agent *"move us to Flagsmith"*. Liftover then:
1. Takes stock of every flag in Unleash and every homegrown environment-variable flag in the code.
2. Rebuilds them in Flagsmith with the Flagsmith CLI. It does a dry run first, and writes production only after you approve the exact change list.
3. Evaluates thousands of sampled users in both systems with their own SDKs.
4. Rewrites the flag reads and opens the PR with the parity report as its body.

## Why

Migrating flags is the job where an agent gets things quietly wrong. Unleash ORs its strategies and ANDs the constraints inside one. Flagsmith's own LaunchDarkly importer gets that shape wrong ([#8721](https://github.com/Flagsmith/flagsmith/issues/8721)), and the importer docs tell you to check the results by hand. Liftover makes that check a machine's job: a migration is done when the code reads Flagsmith and no user's flags changed without someone deciding they should.

## The five steps

| Step | Script | Reads / writes |
|---|---|---|
| 1. Inventory | `inventory.py` | Unleash admin API (read-only) and the code; flags dead flags, flags missing from the source, config values |
| 2. Plan | `plan.py` | offline; every review note needs a decision (`--retire/--skip/--config/--migrate`), and unmappable rules block |
| 3. Apply | `apply.py` | the Flagsmith CLI; dry run, `--execute` for non-production, `--confirm-production <token>` for production, `--rollback` |
| 4. Parity | `parity.py` | Unleash SDK from a bootstrap and the Flagsmith SDK on `flagsmith environment document`, both offline |
| 5. Cutover | `cutover.py` | a worklist of the exact lines to change, then `--check` (no old reads left) and the PR body |

The agent's instructions are in [skills/liftover/SKILL.md](skills/liftover/SKILL.md). Every script prints one line per flag, so the agent reads lines, not JSON.

## How the rules map (checked against self-hosted Flagsmith 2.295.2)

- **OR of strategies:** one segment `<flag>_<env>` per targeted flag and environment, with a root `ANY` rule and one `ALL` child per strategy. The environment default is off; a segment override turns it on.
- **`userId` lists:** `$.identity.identifier IN ...`, which keeps them as rules rather than per-user overrides.
- **Rollout %:** `PERCENTAGE_SPLIT`. **Variants:** multivariate options with the same weights.
- **Numeric, semver, string and negated constraints:** the matching Flagsmith operators, `REGEX` for case-insensitive or prefix/suffix matches, and `NOT_EQUAL` for each value in a `NOT_IN`.
- **Environment-variable flags:** read the code's comparison (`== "true"`, `== "1"`), not just the raw value.
- **Cannot map** (dates, Unleash segments, session stickiness): reported as `UNDECIDED`, never guessed.

## Safety around production

- **Dry run first:** the dry run lists only what differs from the plan. `PROD` marks production lines and the run ends with a token for exactly that list. `--execute` writes development and staging and holds production.
- **Production needs that token:** production is written only with `--confirm-production <token>`, given after the user approves those lines. If the list changes in between, the token no longer matches and the run is refused.
- **Reruns and drift:** reruns are idempotent, so a failed run continues where it stopped. A hand-edited segment shows up as a fix in the next dry run.
- **Rollback:** everything created is tagged `migrated` and recorded, and `--rollback` removes it.
- **Cutover keeps today's behaviour:** unknown flags still answer off (`default_flag_handler`), and the old system stays as a fallback.

## Demo results (sandbox in this repo)

The demo is a small Python shop with 8 Unleash flags, 3 environment-variable settings and one flag read in code but missing from Unleash. The 8 Unleash flags cover a 25% rollout, a user list, two OR'd strategies, an age rule, a three-variant 50/30/20 test, per-environment differences and a dead flag.

- **Inventory:** found all 12 flags with the right review notes (dead `legacy_search`, missing `recommendations_v2`, config value `upload_limit_mb`).
- **Plan and apply:** 10 Flagsmith features and 4 segments. The first run made 29 changes (11 in production), and a rerun reports "nothing to do".
- **Parity, 4,000 users × 2 environments:** 20/20 pass. 17 flag/environment pairs give the same answer for every user. Three hash users (`new_checkout` 25% and `checkout_button` in both environments) and stay within 4 points: for example Unleash 24.6% on vs Flagsmith 26.4%. See [examples/demo-shop-pr.md](examples/demo-shop-pr.md).
- **Bug caught:** changing `pro_reports` to the AND shape of #8721 gives `FAIL pro_reports production: 873 users differ` with example users. The next dry run lists the one-line fix.
- **Cutover:** [examples/demo-shop-cutover.diff](examples/demo-shop-cutover.diff). For 203 users, the shop's pages before and after are identical except the two hashed fields, whose shares stay close.
- **Token use:** with the skill, the agent reads about 15k characters (SKILL.md plus every script's output), roughly 3.8k tokens. The raw data it would otherwise read is about 42.5k characters, roughly 10.6k tokens: the Unleash API JSON, the code, both Flagsmith environment documents, and the plan and parity data. That is an estimate of input only, not an A/B run of two agents. The gap grows with the flag count because the skill prints one line per flag.

## Run the demo

```
docker compose -f sandbox/compose.yml up -d     # Flagsmith 2.295.2 on :8000, Unleash 8.3.1 on :4242
python sandbox/bootstrap.py                     # admin, organisation, API key -> sandbox/.env (git-ignored)
python sandbox/seed_unleash.py                  # the "before" flags
npm install -g @flagsmith/cli && pip install flagsmith UnleashClient
```

Then load `sandbox/.env` into the shell, set `FLAGSMITH_ORGANISATION`, and run the five steps from SKILL.md against `demo-app/`.

## Limits

- **Sources:** Unleash and environment variables only. LaunchDarkly is not built yet.
- **Not converted:** Unleash segments, date constraints and non-user stickiness are reported, not converted.
- **Rollouts and variants:** keep their shares, not their exact users, because the two tools hash differently. The report counts the users who switch.
- **Cutover hints:** written for Python and JavaScript SDK calls.

## Layout

```
skills/liftover/   SKILL.md + scripts (stdlib, except parity.py which needs the two SDKs)
sandbox/           pinned Flagsmith + Unleash, bootstrap, seed, page smoke test
demo-app/          the shop before migration
examples/          cutover diff and PR body from the demo run
tests/             unittest, no server needed
```
