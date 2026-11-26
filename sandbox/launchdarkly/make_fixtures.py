"""Write the demo's LaunchDarkly flags as LaunchDarkly SDK data files, one per environment.

This is the format the LaunchDarkly server-side SDK endpoint returns (`GET https://sdk.launchdarkly.com/sdk/latest-all`
with an environment's SDK key) and what the SDK's file data source reads, so parity.py evaluates them with
LaunchDarkly's own engine and no LaunchDarkly account is needed for the demo.
"""
import json
from pathlib import Path

HERE = Path(__file__).parent


def flag(key, variations, on=True, off=1, fallthrough=None, targets=(), rules=(), prerequisites=()):
    return {"key": key, "version": 1, "on": on, "salt": f"{key}-salt", "variations": list(variations),
            "offVariation": off, "fallthrough": fallthrough or {"variation": 1},
            "targets": [{"values": v, "variation": i, "contextKind": "user"} for v, i in targets], "contextTargets": [],
            "rules": [{"id": f"{key}-r{n}", "clauses": c, **r} for n, (c, r) in enumerate(rules)],
            "prerequisites": list(prerequisites), "trackEvents": False, "trackEventsFallthrough": False,
            "clientSide": False, "deleted": False}


def clause(attr, op, values, negate=False):
    return {"attribute": attr, "op": op, "values": values, "negate": negate, "contextKind": "user"}


def rollout(*weights):  # weights in percent, one per variation index
    return {"rollout": {"variations": [{"variation": i, "weight": int(w * 1000)} for i, w in enumerate(weights)],
                        "bucketBy": "key"}}


BOOL = [True, False]


def flags(env):
    prod = env == "production"
    return {f["key"]: f for f in [
        flag("new_checkout", BOOL, fallthrough=rollout(25, 75) if prod else {"variation": 0}),
        flag("beta_dashboard", BOOL, targets=[(["u-001", "u-002", "u-003"], 0)] if prod else [],
             fallthrough={"variation": 1} if prod else {"variation": 0}),
        # Two rules that both serve true: LaunchDarkly ORs them (Flagsmith's importer ANDs this shape, #8721).
        flag("pro_reports", BOOL, fallthrough={"variation": 1}, rules=[
            ([clause("plan", "in", ["pro", "enterprise"]), clause("country", "in", ["IN", "US"])], {"variation": 0}),
            ([clause("key", "in", ["vip-1"])], {"variation": 0})]),
        flag("checkout_button", ["buy_now", "get_it_today", "add_to_bag"], off=0, fallthrough=rollout(50, 30, 20)),
        flag("age_gate", BOOL, on=prod, rules=[([clause("age", "greaterThanOrEqual", [18])], {"variation": 0})]),
        # Ordered rules with different values: first match wins, so an Indian enterprise customer pays in INR.
        flag("pricing_tier", ["usd", "inr", "invoice"], off=0, fallthrough={"variation": 0}, rules=[
            ([clause("country", "in", ["IN"])], {"variation": 1}),
            ([clause("plan", "in", ["enterprise"])], {"variation": 2})]),
        flag("maintenance_mode", BOOL, on=False),
        flag("legacy_search", BOOL, fallthrough={"variation": 0}),
        # Depends on another flag: Flagsmith has no prerequisites, so liftover must refuse to guess.
        flag("upsell_banner", BOOL, fallthrough={"variation": 0},
             prerequisites=[{"key": "checkout_button", "variation": 0}]),
    ]}


if __name__ == "__main__":
    for env in ("development", "production"):
        (HERE / f"{env}.json").write_text(json.dumps({"flags": flags(env), "segments": {}}, indent=1))
    print("wrote development.json, production.json")
