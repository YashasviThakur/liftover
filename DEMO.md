# Demo video script (about 2:45)

Record one terminal window, large font, with the Flagsmith dashboard open in a second window for the cuts marked [DASH].
Everything below is a command in this repo's sandbox (`docker compose -f sandbox/compose.yml up -d`, then the steps in the README).

| Time | Say | Show |
|---|---|---|
| 0:00 | "Moving feature flags is the job where an agent quietly gets things wrong. Unleash ORs its strategies. Flagsmith's own importer ANDs them, so users lose access and nothing errors. Liftover does the whole move and proves nothing changed." | Title card, then the shop's Unleash dashboard with 8 flags [DASH] |
| 0:15 | "I ask my agent: move us to Flagsmith." | Claude Code with the `liftover` skill; the prompt typed |
| 0:25 | "Step 1 reads Unleash, the code and the env files. 12 flags, and it flags three that need a human: one dead, one missing from Unleash, one that is config not a flag." | `inventory.py` table |
| 0:45 | "Step 2 plans it offline. Every review note needs my decision, and rules it can't map are refused, never guessed. I ask to keep rollouts on the same users." | `plan.py ... --preserve-rollouts`, the `UNDECIDED` lines, then the final plan with `preserved new_checkout` |
| 1:05 | "Step 3 is a dry run. Production lines are marked PROD, and writing them needs a token for exactly this list." | `apply.py` dry run, then `--execute` (non-prod written, prod held), then `--confirm-production <token>` [DASH] shows the segments |
| 1:35 | "Step 4 is the proof. Both systems are evaluated by their own SDKs for 4,000 users in each environment." | `parity.py`: 20/20 PASS, `new_checkout` "same for every user" |
| 1:55 | "Now I break it on purpose, the way Flagsmith's importer does: AND instead of OR. 873 users differ, with examples." | Edit one rule, `parity.py` shows `FAIL pro_reports ... 873 users differ`, fix, rerun green |
| 2:15 | "Step 5 lists the exact lines to change, rewrites them and opens the PR with the parity report as its body. Rollback is one command." | `cutover.py`, the diff, `gh pr create`, `apply.py --rollback` |
| 2:35 | "Same agent, same task. Without the skill: 124k tokens, 19 calls, rollouts on different users, no proof. With it: 86k tokens, 9 calls, 20 of 20 checks, same users, rollback ready." | The benchmark table from the README |

Notes
- The injected bug is one line: change the `ANY` root of `pro_reports_production` to `ALL` in the Flagsmith dashboard.
- Say the limits out loud: variants and LaunchDarkly rollouts keep their share but not the exact users, and the report counts who switches.
