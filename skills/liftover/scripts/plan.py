"""Liftover step 2: turn .liftover/inventory.json into a Flagsmith change list. Writes nothing to any server.

  python plan.py [--retire a,b] [--skip c] [--config d] [--migrate e] [--env-map development=Development]

Every inventory row with a review note must be decided with exactly one of --retire / --skip / --config / --migrate.
Writes .liftover/plan.json (input to apply.py) and prints one line per flag. Exit 2 if anything is undecided.

Mapping (checked against self-hosted Flagsmith 2.295.2):
- Unleash ORs strategies and ANDs constraints inside one. Each flag+environment that needs targeting gets one segment
  whose root rule is ANY with one ALL child per strategy. Flagsmith's LaunchDarkly importer ANDs these (issue #8721).
- userId -> $.identity.identifier, so user lists stay segment rules, not identity overrides.
- rollout % -> PERCENTAGE_SPLIT on $.identity.key. The hash differs from Unleash: the same share of users is on,
  but not the same users. parity.py measures this.
- variants -> multivariate options with the same weights.
- LaunchDarkly: targets and rules are checked in order and the first match wins, so each becomes a segment
  override at the same priority; the fallthrough is the environment default. Prerequisites have no equivalent.
"""
import argparse
import json
import re
import sys
from pathlib import Path

USER_ATTR = {"userId": "$.identity.identifier"}
NO_EQUIVALENT_ATTR = {"sessionId", "remoteAddress", "currentTime", "appName", "environment"}
# Unleash numeric/semver operator -> (Flagsmith operator, Flagsmith operator when inverted)
COMPARE = {"NUM_EQ": ("EQUAL", "NOT_EQUAL"), "NUM_GT": ("GREATER_THAN", "LESS_THAN_INCLUSIVE"),
           "NUM_GTE": ("GREATER_THAN_INCLUSIVE", "LESS_THAN"), "NUM_LT": ("LESS_THAN", "GREATER_THAN_INCLUSIVE"),
           "NUM_LTE": ("LESS_THAN_INCLUSIVE", "GREATER_THAN"), "SEMVER_EQ": ("EQUAL", "NOT_EQUAL"),
           "SEMVER_GT": ("GREATER_THAN", "LESS_THAN_INCLUSIVE"), "SEMVER_LT": ("LESS_THAN", "GREATER_THAN_INCLUSIVE")}
# Flagsmith REGEX uses re.match (anchored at the start).
STR_PATTERN = {"STR_CONTAINS": ".*({})", "STR_STARTS_WITH": "({})", "STR_ENDS_WITH": ".*({})$"}


class Unmappable(Exception):
    pass


def cond(prop, op, value):
    return {"property": prop, "operator": op, "value": value}


def map_condition(c, warnings):
    attr, op, vals, inv, ci = c["attr"], c["op"], [str(v) for v in c["values"]], c["inverted"], c["case_insensitive"]
    if attr in NO_EQUIVALENT_ATTR:
        raise Unmappable(f"context field {attr!r} has no Flagsmith equivalent")
    prop = USER_ATTR.get(attr, attr)
    alt = "|".join(re.escape(v) for v in vals)
    neg = inv ^ (op == "NOT_IN")
    if neg:
        warnings.append(f"negated rule on {attr}: users without {attr} may differ, check parity")
    if op in ("IN", "NOT_IN"):
        if any("," in v for v in vals):
            raise Unmappable(f"{attr} value contains a comma (Flagsmith IN is comma-separated)")
        if ci:
            if neg:
                raise Unmappable(f"case-insensitive NOT_IN on {attr}")
            return [cond(prop, "REGEX", f"(?i)({alt})$")]
        return [cond(prop, "NOT_EQUAL", v) for v in vals] if neg else [cond(prop, "IN", ",".join(vals))]
    if op in STR_PATTERN:
        if inv:
            if op == "STR_CONTAINS" and not ci:
                return [cond(prop, "NOT_CONTAINS", v) for v in vals]
            raise Unmappable(f"inverted {op} on {attr}")
        return [cond(prop, "REGEX", ("(?i)" if ci else "") + STR_PATTERN[op].format(alt))]
    if op in COMPARE:
        if len(vals) != 1:
            raise Unmappable(f"{op} on {attr} with {len(vals)} values")
        suffix = ":semver" if op.startswith("SEMVER") else ""
        return [cond(prop, COMPARE[op][inv], vals[0] + suffix)]
    if op == "MATCHES" and not inv:  # LaunchDarkly regex: a match anywhere in the value
        return [cond(prop, "REGEX", ".*(?:" + "|".join(vals) + ")")]
    raise Unmappable(f"{'negated ' if inv else ''}operator {op} on {attr}")


def map_rule(r, warnings):
    """One Unleash strategy -> the conditions of one ALL rule ([] means everyone)."""
    if r["strategy"] not in ("flexibleRollout", "default"):
        raise Unmappable(f"strategy {r['strategy']!r}")
    if r["segments"]:
        # shortcut: Unleash segments are not expanded, inline them in Unleash or add expansion here when needed.
        raise Unmappable(f"uses Unleash segments {r['segments']}")
    out = [x for c in r["conditions"] for x in map_condition(c, warnings)]
    if r["rollout_percent"] < 100:
        if r.get("stickiness") not in (None, "default", "userId"):
            raise Unmappable(f"rollout sticky on {r['stickiness']!r}; Flagsmith splits by identity only")
        out.append(cond("$.identity.key", "PERCENTAGE_SPLIT", f"{r['rollout_percent']:g}"))
    return out


def env_var_enabled(f, value):
    """An env-var flag is on when the code's comparison is true, e.g. == "true"."""
    tests = {m.groups() for ref in f["code_refs"] for m in re.finditer(r"""(==|!=)\s*["']([^"']*)["']""", ref)}
    if len(tests) != 1:
        raise Unmappable("code compares the variable in " + ("no recognised way" if not tests else "different ways"))
    (op, literal), = tests
    return (value == literal) == (op == "==")


def plan_flag(f, envs, as_config):
    """-> (feature, states, segments, warnings)."""
    warnings, segments, states = [], [], []
    feature = {"name": f["key"], "description": f.get("description") or f"migrated from {f['source']}",
               "type": "STANDARD", "initial_value": None, "mv_options": [],
               "kind": "value" if as_config else ("multivariate" if f["kind"] == "multivariate" else "boolean")}
    if f["source"] == "env":
        for env in envs:
            e = f["environments"].get(env)
            value = e["value"] if e else None
            if as_config:
                states.append({"env": env, "enabled": value is not None, "value": value})
            else:
                states.append({"env": env, "enabled": env_var_enabled(f, value), "value": None})
            if e is None:
                warnings.append(f"not set in {env}: planned as {'empty' if as_config else 'off'}")
        return feature, states, segments, warnings

    if f["kind"] == "multivariate":
        feature["type"] = "MULTIVARIATE"
        seen = {}
        for v in f["variants"]:
            seen.setdefault(str(v["value"] if v["value"] is not None else v["key"]), v["key"])
        feature["mv_options"] = [{"value": val, "unleash_variant": name} for val, name in seen.items()]
        feature["initial_value"] = next(iter(seen))  # gets any rounding remainder

    for env in envs:
        e = f["environments"].get(env)
        if e is None:
            states.append({"env": env, "enabled": False, "value": None})
            warnings.append(f"not in {f['source']} {env}: planned as off")
            continue
        state = {"env": env, "enabled": False, "value": None}
        variant_sets = {json.dumps(r["variants"]) for r in e["rules"] if r.get("variants")}
        if len(variant_sets) > 1:
            raise Unmappable(f"different variant weights per strategy in {env}")
        if variant_sets:
            vs = json.loads(variant_sets.pop())
            state["mv_pcts"] = {str(v["value"] if v["value"] is not None else v["key"]): v["weight_pct"] for v in vs}
        if e["enabled"]:
            children = [map_rule(r, warnings) for r in e["rules"]]
            if not children or any(c == [] for c in children):
                state["enabled"] = True  # some strategy matches everyone
            else:
                name = f"{f['key']}_{env}"
                segments.append({"name": name, "flag": f["key"], "env": env, "rules": [
                    {"type": "ANY", "conditions": [],
                     "rules": [{"type": "ALL", "rules": [], "conditions": c} for c in children]}]})
                state["segment_overrides"] = [{"segment": name, "enabled": True, "value": None, "priority": 0}]
        states.append(state)
    return feature, states, segments, warnings


def plan_ld_flag(f, envs):
    """LaunchDarkly: targets, then rules in order (first match wins), then fallthrough. Each becomes a segment
    override at the same priority, so Flagsmith checks them in the same order. -> (feature, states, segments, warnings)"""
    warnings, segments, states = [], [], []
    kind, variations = f["kind"], f["variations"]
    feature = {"name": f["key"], "description": f.get("description") or "migrated from launchdarkly",
               "type": "MULTIVARIATE" if kind == "multivariate" else "STANDARD", "kind": kind,
               "initial_value": None, "mv_options": []}
    if kind == "multivariate":
        feature["mv_options"] = [{"value": str(v)} for v in variations]
        feature["initial_value"] = str(variations[0])
    result = lambda v: (bool(v), None) if kind == "boolean" else (True, str(v))
    split = lambda pct: cond("$.identity.key", "PERCENTAGE_SPLIT", f"{pct:g}")

    for env in envs:
        e = f["environments"].get(env)
        if e is None:
            states.append({"env": env, "enabled": False, "value": None})
            warnings.append(f"not in launchdarkly {env}: planned as off")
            continue
        ld = e["ld"]
        if ld["prerequisites"]:
            raise Unmappable(f"depends on prerequisite flags {[p['key'] for p in ld['prerequisites']]} in {env}")
        if not e["enabled"]:
            off = ld["off_result"]
            en, val = result(off) if off is not None else (False, None)  # no off variation: the code default
            states.append({"env": env, "enabled": en, "value": val})
            continue

        overrides = []  # [conditions of each OR-ed child, enabled, value], in LaunchDarkly's evaluation order

        def serve(conds, en, val):
            if overrides and overrides[-1][1:] == [en, val]:
                overrides[-1][0].append(conds)  # neighbours serving the same value: one segment, OR-ed
            else:
                overrides.append([[conds], en, val])

        for t in ld["targets"]:
            serve([cond("$.identity.identifier", "IN", ",".join(t["values"]))], *result(t["result"]))
        for r in ld["rules"]:
            conds = [x for c in r["conditions"] for x in map_condition(c, warnings)]
            if not conds:
                raise Unmappable(f"a rule with no clauses in {env}")
            if r["rollout"] is None:
                serve(conds, *result(r["result"]))
            elif kind == "boolean":
                share = sum(p["weight_pct"] for p in r["rollout"] if p["result"] is True)
                serve(conds + [split(share)], True, None)
                serve(conds, False, None)
            else:
                raise Unmappable(f"a percentage rollout inside a targeting rule in {env}")

        state = {"env": env, "enabled": False, "value": None}
        fall = ld["fallthrough"]
        if fall["rollout"] is None:
            state["enabled"], state["value"] = result(fall["result"])
        elif kind == "boolean":
            share = sum(p["weight_pct"] for p in fall["rollout"] if p["result"] is True)
            serve([split(share)], True, None)
        else:
            if overrides:
                # shortcut: variant weights per segment override are not planned yet; add them when a user needs it.
                raise Unmappable(f"variant rollout combined with targeting in {env}")
            state["enabled"] = True
            state["mv_pcts"] = {str(p["result"]): p["weight_pct"] for p in fall["rollout"]}
        # A trailing override that serves the default changes nothing.
        while overrides and overrides[-1][1:] == [state["enabled"], state["value"]]:
            overrides.pop()
        state["segment_overrides"] = []
        for i, (children, en, val) in enumerate(overrides):
            name = f"{f['key']}_{env}" + (f"_{i + 1}" if len(overrides) > 1 else "")
            segments.append({"name": name, "flag": f["key"], "env": env, "rules": [
                {"type": "ANY", "conditions": [], "rules": [{"type": "ALL", "rules": [], "conditions": c}
                                                            for c in children]}]})
            state["segment_overrides"].append({"segment": name, "enabled": en, "value": val, "priority": i})
        states.append(state)
    return feature, states, segments, warnings


def summarise(feature, states):
    cells = []
    show = lambda en, val: val if val is not None else ("on" if en else "off")
    for s in states:
        cells.append(f"{s['env']}: {show(s['enabled'], s.get('value'))}")
        for o in s.get("segment_overrides", []):
            cells[-1] += f", {show(o['enabled'], o['value'])} for {o['segment']}"
        if s.get("mv_pcts"):
            cells[-1] += " [" + "/".join(f"{k} {v:g}" for k, v in s["mv_pcts"].items()) + "]"
    return f"{feature['name']} | {feature['type']} | " + " | ".join(cells)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory", type=Path, default=Path(".liftover/inventory.json"))
    ap.add_argument("--out", type=Path, default=Path(".liftover/plan.json"))
    for d in ("retire", "skip", "config", "migrate"):
        ap.add_argument(f"--{d}", default="", help="comma-separated flag keys")
    ap.add_argument("--env-map", default="", help="source=Flagsmith environment names, e.g. production=Production")
    a = ap.parse_args()

    flags = json.loads(a.inventory.read_text())["flags"]
    decided = {}
    for d in ("retire", "skip", "config", "migrate"):
        for key in filter(None, getattr(a, d).split(",")):
            if key not in flags:
                sys.exit(f"--{d} {key}: not in the inventory")
            if key in decided:
                sys.exit(f"{key}: decided twice (--{decided[key]} and --{d})")
            decided[key] = d
    env_map = dict(p.split("=", 1) for p in filter(None, a.env_map.split(",")))
    envs = sorted({e for f in flags.values() for e in f["environments"]})

    plan = {"environments": [env_map.get(e, e) for e in envs], "env_map": {e: env_map.get(e, e) for e in envs},
            "inventory": str(a.inventory), "features": [], "states": [], "segments": [],
            "not_migrated": [], "warnings": {}}
    undecided = []
    for key, f in sorted(flags.items()):
        d = decided.get(key)
        if d in ("retire", "skip"):
            plan["not_migrated"].append({"flag": key, "decision": d, "reason": f.get("needs_review")})
            continue
        if f.get("needs_review") and not d:
            undecided.append(f"{key}: {f['needs_review']}")
            continue
        if f["source"] == "code-only":
            undecided.append(f"{key}: nothing to migrate from (code-only); use --skip or --retire")
            continue
        try:
            feature, states, segments, warnings = (plan_ld_flag(f, envs) if f["source"] == "launchdarkly"
                                                   else plan_flag(f, envs, as_config=(d == "config")))
        except Unmappable as e:
            undecided.append(f"{key}: cannot map automatically: {e}; rebuild by hand, then --skip it")
            continue
        for s in states:
            s["env"] = env_map.get(s["env"], s["env"])
        for s in segments:
            s["env"] = env_map.get(s["env"], s["env"])
        feature["source"] = f["source"]
        plan["features"].append(feature)
        plan["states"] += [{"feature": key, **s} for s in states]
        plan["segments"] += segments
        if warnings:
            plan["warnings"][key] = warnings

    plan["complete"] = not undecided  # apply.py refuses an incomplete plan
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps(plan, indent=2))
    print(f"{len(plan['features'])} features, {len(plan['segments'])} segments, "
          f"{len(plan['not_migrated'])} not migrated -> {a.out}")
    for feature in plan["features"]:
        print(summarise(feature, [s for s in plan["states"] if s["feature"] == feature["name"]]))
    for n in plan["not_migrated"]:
        print(f"{n['flag']} | {n['decision']}")
    for key, ws in plan["warnings"].items():
        for w in ws:
            print(f"warning {key}: {w}")
    for u in undecided:
        print(f"UNDECIDED {u}")
    if undecided:
        sys.exit(2)


if __name__ == "__main__":
    main()
