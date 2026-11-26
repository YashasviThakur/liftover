# How liftover translates rules

Read this only when you need to explain a row of the plan to the user. Checked against self-hosted Flagsmith 2.295.2.

## Unleash

- Strategies are OR-ed; constraints inside one strategy are AND-ed. Each targeted flag+environment becomes one segment
  `<flag>_<env>` with a root `ANY` rule and one `ALL` child per strategy. The environment default is off and a segment
  override turns it on. (Flagsmith's LaunchDarkly importer ANDs this shape: issue #8721.)
- A strategy that matches everyone makes the environment default on, with no segment.
- `userId` becomes `$.identity.identifier`, so user lists stay rules, not per-user overrides.
- A rollout % becomes `PERCENTAGE_SPLIT` on `$.identity.key`. Variants become multivariate options with the same
  weights.
- Cannot map: Unleash segments, date constraints, stickiness on anything but the user, `sessionId`, `remoteAddress`,
  `currentTime`.

## LaunchDarkly

- Evaluation order is off → targets → rules in order → fallthrough, and the first match wins. Each target list and
  rule becomes a segment override with the same priority (0 first), so Flagsmith checks them in the same order.
  Neighbouring rules that serve the same value share one segment (`ANY` of `ALL`). Trailing overrides that serve the
  default are dropped.
- The fallthrough is the environment default. A flag that is off serves its off variation everywhere.
- Boolean flags map to on/off. Other variations map to a Flagsmith value, or to multivariate weights when the
  fallthrough is a percentage rollout.
- A boolean percentage rollout inside a rule becomes two overrides: the rule plus `PERCENTAGE_SPLIT` serves true, then
  the rule alone serves false. That keeps "matched the rule, lost the coin flip" from falling through to later rules.
- The clause attribute `key` becomes `$.identity.identifier`.
- Cannot map: prerequisites, segment-match clauses, date clauses, contexts other than `user`, and variant rollouts
  inside rules or combined with targeting.

## Operators

| Source | Flagsmith |
|---|---|
| in / IN | `IN` (comma-separated, so values with commas cannot map) |
| NOT_IN or a negated in | one `NOT_EQUAL` per value |
| startsWith, endsWith, contains | `REGEX` (Flagsmith anchors at the start, so `.*` is added); a negated contains is `NOT_CONTAINS` |
| matches (LaunchDarkly regex) | `REGEX` `.*(?:pattern)` |
| case-insensitive | `REGEX` with `(?i)` |
| numeric <, <=, >, >=, = | `LESS_THAN`, `LESS_THAN_INCLUSIVE`, `GREATER_THAN`, `GREATER_THAN_INCLUSIVE`, `EQUAL` (negated: the opposite) |
| semver | the same, with the value suffixed `:semver` |

Negated rules print a warning: a user without the attribute may be treated differently, and the parity check shows it.

## Environment variables

The flag is on when the code's comparison is true (`== "true"`, `!= "0"`). A variable compared in different ways, or
not compared at all, needs a decision. Config values (`--config`) become Flagsmith values per environment; unset
means no value, and the cutover keeps the code's old default.

## Self-hosted notes

- A free organisation holds one project. Use a separate organisation, or remove the old project, to migrate twice.
- Creating an organisation with an API key fails in 2.295.2 (500). Create it from the dashboard.
- Segment rules need a property on `PERCENTAGE_SPLIT` (a blank one is rejected), so liftover uses `$.identity.key`.
