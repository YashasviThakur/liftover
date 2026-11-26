"""Seed the sandbox Unleash with the flags the demo shop uses: the "before" state of a migration.

Covers the cases a migration has to get right: plain on/off per environment, a percentage rollout,
user targeting, OR-ed strategies (the shape Flagsmith's LaunchDarkly importer gets wrong, issue #8721),
numeric constraints, weighted variants, and a flag no code reads any more.
Idempotent: deletes and recreates each flag. Writes client tokens to sandbox/.env.
"""
import json
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://localhost:4242/api"
ADMIN = "*:*.liftover-sandbox-admin"
PROJECT = "default"
ENV_FILE = Path(__file__).with_name(".env")


def call(method, path, body=None):
    req = urllib.request.Request(f"{BASE}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": ADMIN, "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as r:
            raw = r.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{method} {path} -> {e.code}: {e.read()[:300]!r}")


def rollout(percent, group, constraints=(), variants=()):
    return {"name": "flexibleRollout",
            "parameters": {"rollout": str(percent), "stickiness": "userId", "groupId": group},
            "constraints": list(constraints), "variants": list(variants)}


def c(context, operator, values):
    key = "value" if operator.startswith("NUM_") else "values"
    return {"contextName": context, "operator": operator,
            key: values if key == "values" else str(values)}


# flag -> (type, {env: (enabled, [strategies])}, toggle-level variants)
FLAGS = {
    "new_checkout": ("release", {
        "development": (True, [rollout(100, "new_checkout")]),
        "production": (True, [rollout(25, "new_checkout")]),
    }, []),
    "beta_dashboard": ("release", {
        "development": (True, [rollout(100, "beta_dashboard")]),
        "production": (True, [rollout(100, "beta_dashboard", [c("userId", "IN", ["u-001", "u-002", "u-003"])])]),
    }, []),
    "pro_reports": ("release", {
        "development": (True, [rollout(100, "pro_reports")]),
        "production": (True, [
            rollout(100, "pro_reports", [c("plan", "IN", ["pro", "enterprise"]), c("country", "IN", ["IN", "US"])]),
            rollout(100, "pro_reports", [c("userId", "IN", ["vip-1"])]),
        ]),
    }, []),
    "checkout_button": ("experiment", {
        "development": (True, [rollout(100, "checkout_button")]),
        "production": (True, [rollout(100, "checkout_button")]),
    }, [("buy_now", 500), ("get_it_today", 300), ("add_to_bag", 200)]),
    "age_gate": ("permission", {
        "development": (False, [rollout(100, "age_gate", [c("age", "NUM_GTE", 18)])]),
        "production": (True, [rollout(100, "age_gate", [c("age", "NUM_GTE", 18)])]),
    }, []),
    "holiday_banner": ("release", {
        "development": (True, [rollout(100, "holiday_banner")]),
        "production": (False, [rollout(100, "holiday_banner")]),
    }, []),
    "maintenance_mode": ("kill-switch", {
        "development": (False, [rollout(100, "maintenance_mode")]),
        "production": (False, [rollout(100, "maintenance_mode")]),
    }, []),
    "legacy_search": ("release", {
        "development": (True, [rollout(100, "legacy_search")]),
        "production": (True, [rollout(100, "legacy_search")]),
    }, []),
}

CONTEXT_FIELDS = ["plan", "country", "age"]


def main():
    existing_ctx = {f["name"] for f in call("GET", "/admin/context")}
    for name in CONTEXT_FIELDS:
        if name not in existing_ctx:
            call("POST", "/admin/context", {"name": name, "stickiness": False})

    existing = {f["name"] for f in call("GET", f"/admin/projects/{PROJECT}/features")["features"]}
    for name, (ftype, envs, variants) in FLAGS.items():
        if name in existing:
            call("DELETE", f"/admin/projects/{PROJECT}/features/{name}")
            call("DELETE", f"/admin/archive/{name}")
        call("POST", f"/admin/projects/{PROJECT}/features", {"name": name, "type": ftype})
        for env, (enabled, strategies) in envs.items():
            for s in strategies:
                if variants:
                    # Unleash spreads "variable" variants evenly over what "fix" ones leave, so the
                    # first variant takes the remainder and the rest are fixed.
                    s = {**s, "variants": [{"name": v, "weight": w,
                                            "weightType": "variable" if i == 0 else "fix",
                                            "stickiness": "userId",
                                            "payload": {"type": "string", "value": v}}
                                           for i, (v, w) in enumerate(variants)]}
                call("POST", f"/admin/projects/{PROJECT}/features/{name}/environments/{env}/strategies", s)
            call("POST", f"/admin/projects/{PROJECT}/features/{name}/environments/{env}/{'on' if enabled else 'off'}")

    # One backend (client) token per environment, used for offline evaluation in the parity check.
    env_lines = dict(l.split("=", 1) for l in ENV_FILE.read_text().splitlines() if "=" in l) if ENV_FILE.exists() else {}
    tokens = call("GET", "/admin/api-tokens")["tokens"]
    for env in ("development", "production"):
        var = f"UNLEASH_CLIENT_TOKEN_{env.upper()}"
        have = [t for t in tokens if t["type"] == "client" and t["environment"] == env]
        if var not in env_lines:
            tok = have[0] if have else call("POST", "/admin/api-tokens", {
                "tokenName": f"liftover-{env}", "type": "client", "environment": env, "projects": [PROJECT]})
            env_lines[var] = tok["secret"]
    env_lines.setdefault("UNLEASH_URL", "http://localhost:4242/api")
    env_lines.setdefault("UNLEASH_ADMIN_TOKEN", ADMIN)
    ENV_FILE.write_text("".join(f"{k}={v}\n" for k, v in env_lines.items()))
    print(f"ok: seeded {len(FLAGS)} Unleash flags in project {PROJECT!r}; client tokens in {ENV_FILE.name}")


if __name__ == "__main__":
    main()
