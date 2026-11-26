# Liftover

An agent skill that moves a codebase's feature flags to Flagsmith from LaunchDarkly, Unleash or homegrown environment variables, and proves nothing changed for your users.

Ask your agent *"move us to Flagsmith"*. Liftover then:
1. Takes stock of every flag in LaunchDarkly or Unleash and every homegrown environment-variable flag in the code.
2. Rebuilds them in Flagsmith with the Flagsmith CLI. It does a dry run first, and writes production only after you approve the exact change list.
3. Evaluates thousands of sampled users in both systems with their own SDKs.
4. Rewrites the flag reads and opens the PR with the parity report as its body.

## Why

Migrating flags is the job where an agent gets things quietly wrong. LaunchDarkly ORs its rules and checks them in order, with the first match winning. Unleash ORs its strategies and ANDs the constraints inside one. Flagsmith's own LaunchDarkly importer gets the OR shape wrong ([#8721](https://github.com/Flagsmith/flagsmith/issues/8721)), and the importer docs tell you to check the results by hand. Liftover makes that check a machine's job: a migration is done when the code reads Flagsmith and no user's flags changed without someone deciding they should. The check uses each tool's own SDK, so it tests the real engines, not Liftover's reading of them.

## The five steps

| Step | Script | Reads / writes |
|---|---|---|
| 1. Inventory | `inventory.py` | LaunchDarkly SDK data files or the Unleash admin API (read-only), and the code; flags dead flags, flags missing from the source, config values |
| 2. Plan | `plan.py` | offline; every review note needs a decision (`--retire/--skip/--config/--migrate`), and unmappable rules block |
| 3. Apply | `apply.py` | the Flagsmith CLI; dry run, `--execute` for non-production, `--confirm-production <token>` for production, `--rollback` |
| 4. Parity | `parity.py` | the LaunchDarkly or Unleash SDK against the Flagsmith SDK on `flagsmith environment document`, all offline |
| 5. Cutover | `cutover.py` | a worklist of the exact lines to change, then `--check` (no old reads left) and the PR body |

The agent's instructions are in [skills/liftover/SKILL.md](skills/liftover/SKILL.md), about 1.9k tokens, and it passes the Agent Skills validator (`agentskills validate`). Every script prints one line per flag, so the agent reads lines, not JSON.

## Install

Copy `skills/liftover/` into your agent's skills folder: for Claude Code that is `~/.claude/skills/liftover/` (or `.claude/skills/` in a repo). Any agent that reads the [Agent Skills](https://agentskills.io) format can load it the same way. You also need the Flagsmith CLI v2 (`npm install -g @flagsmith/cli`) and, for the parity step, `pip install flagsmith` plus `UnleashClient` or `launchdarkly-server-sdk`.

## How the rules map (checked against self-hosted Flagsmith 2.295.2)

The full table is in [references/mapping.md](skills/liftover/references/mapping.md). In short:
- **Unleash OR of strategies:** one segment per targeted flag and environment, with a root `ANY` rule and one `ALL` child per strategy.
- **LaunchDarkly ordered rules:** targets and rules become segment overrides at the same priority, so Flagsmith checks them in the same order and the first match still wins. Neighbouring rules that serve the same value share one OR'd segment. The fallthrough becomes the environment default.
- **User lists:** `$.identity.identifier IN ...`, which keeps them as rules rather than per-user overrides.
- **Rollout %:** `PERCENTAGE_SPLIT`. **Variants:** multivariate options with the same weights.
- **Environment-variable flags:** read the code's comparison (`== "true"`, `== "1"`), not just the raw value.
- **Cannot map** (LaunchDarkly prerequisites, dates, source-side segments, non-user contexts): reported as `UNDECIDED`, never guessed.

## Safety around production

- **Dry run first:** the dry run lists only what differs from the plan. `PROD` marks production lines and the run ends with a token for exactly that list. `--execute` writes development and staging and holds production.
- **Production needs that token:** production is written only with `--confirm-production <token>`, given after the user approves those lines. If the list changes in between, the token no longer matches and the run is refused.
- **Reruns and drift:** reruns are idempotent, so a failed run continues where it stopped. A hand-edited segment shows up as a fix in the next dry run.
- **Rollback:** everything created is tagged `migrated` and recorded, and `--rollback` removes it.
- **Cutover keeps today's behaviour:** unknown flags still answer off (`default_flag_handler`), and the old system stays as a fallback.

## Demo results (sandbox in this repo)

**Unleash demo:** a small Python shop with 8 Unleash flags, 3 environment-variable settings and one flag read in code but missing from Unleash. The 8 Unleash flags cover a 25% rollout, a user list, two OR'd strategies, an age rule, a three-variant 50/30/20 test, per-environment differences and a dead flag.

- **Inventory:** found all 12 flags with the right review notes (dead `legacy_search`, missing `recommendations_v2`, config value `upload_limit_mb`).
- **Plan and apply:** 10 Flagsmith features and 4 segments. The first run made 29 changes (11 in production), and a rerun reports "nothing to do".
- **Parity, 4,000 users × 2 environments:** 20/20 pass. 17 flag/environment pairs give the same answer for every user. Three hash users (`new_checkout` 25% and `checkout_button` in both environments) and stay within 4 points: for example Unleash 24.6% on vs Flagsmith 25.2%. See [examples/demo-shop-pr.md](examples/demo-shop-pr.md).
- **Bug caught:** changing `pro_reports` to the AND shape of #8721 gives `FAIL pro_reports production: 873 users differ` with example users. The next dry run lists the one-line fix.
- **Cutover:** [examples/demo-shop-cutover.diff](examples/demo-shop-cutover.diff). For 203 users, the shop's pages before and after are identical except the two hashed fields, whose shares stay close.
**LaunchDarkly demo:** the same shop on LaunchDarkly ([demo-app-ld/](demo-app-ld/shop.py), flags in [sandbox/launchdarkly/](sandbox/launchdarkly/make_fixtures.py) as LaunchDarkly SDK data, so no account is needed). It adds `pricing_tier`, whose ordered rules serve INR before invoice, and `upsell_banner`, which depends on another flag.

- **Plan:** 7 features and 9 segments. `pricing_tier` keeps its rule order as override priorities 0 and 1, and `pro_reports` becomes one OR'd segment. `upsell_banner` is refused ("depends on prerequisite flags") instead of guessed.
- **Parity, checked by LaunchDarkly's own SDK:** 14/14 pass, including the ordered `pricing_tier` for every user. See [examples/launchdarkly-parity.md](examples/launchdarkly-parity.md).

**Both demos:**
- **Token use (measured):** a fresh agent given only the skill and the Unleash shop finished all five steps in **86,990 tokens**, 11 tool calls, 3 minutes 4 seconds (Claude Sonnet 5.5 subagent, 2026-11-26; the count is the harness's total for that agent). Result: 10 flags migrated, 1 retired, 1 skipped, parity 20/20, cutover committed. Earlier estimate of the skill's input alone: about 3.8k tokens against 10.6k for the raw data.

## Run the demo

```
docker compose -f sandbox/compose.yml up -d     # Flagsmith 2.295.2 on :8000, Unleash 8.3.1 on :4242
python sandbox/bootstrap.py                     # admin, two organisations, API keys -> sandbox/.env (git-ignored)
python sandbox/seed_unleash.py                  # the Unleash "before" flags
python sandbox/launchdarkly/make_fixtures.py    # the LaunchDarkly "before" flags, as SDK data files
npm install -g @flagsmith/cli && pip install flagsmith UnleashClient launchdarkly-server-sdk
```

Then load `sandbox/.env` into the shell, set `FLAGSMITH_ORGANISATION`, and run the five steps from SKILL.md against `demo-app/` or `demo-app-ld/`. A free self-hosted organisation holds one project, so the LaunchDarkly demo uses the second organisation (`FLAGSMITH_LD_ORG_ID`, `FLAGSMITH_LD_API_KEY`).

## Limits

- **Sources:** LaunchDarkly (from SDK data files), Unleash and environment variables.
- **Not converted:** LaunchDarkly prerequisites and segment-match clauses, Unleash segments, date constraints and non-user stickiness are reported, not converted.
- **Rollouts and variants:** keep their shares, not their exact users, because the two tools hash differently. The report counts the users who switch.
- **Cutover hints:** written for Python and JavaScript SDK calls.

## Layout

```
skills/liftover/   SKILL.md, references/mapping.md, scripts (stdlib, except parity.py which needs the SDKs)
sandbox/           pinned Flagsmith + Unleash, bootstrap, seeds, LaunchDarkly fixtures, page smoke test
demo-app/          the shop on Unleash, before migration
demo-app-ld/       the shop on LaunchDarkly, before migration
examples/          cutover diff, PR body and parity reports from the demo runs
tests/             unittest, no server needed
```
