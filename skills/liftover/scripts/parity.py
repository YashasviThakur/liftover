"""Liftover step 4: prove sampled users get the same flags from Flagsmith as from the source system.

  python parity.py --project NAME [--users 4000] [--seed 7] [--tolerance 4]

Both sides are evaluated offline by their own official SDKs, so this tests the real engines, not liftover's reading:
- Unleash: UnleashClient bootstrapped from /api/client/features (one client token per environment,
  UNLEASH_CLIENT_TOKEN_<ENV>), no network after that.
- Flagsmith: the Python SDK in offline mode on `flagsmith environment document`.
- Environment-variable flags: the value in the env file, compared the way the code compares it.

Flags with a percentage rollout or variants hash users differently in the two tools, so those pass when the share of
users on (and in each variant) is within --tolerance percentage points; the report also counts users who switch.
Every other flag must match for every sampled user. Writes .liftover/parity.json and .liftover/parity.md (PR body).
Needs: pip install flagsmith UnleashClient. Exit 1 on any failure.
"""
import argparse
import json
import os
import random
import sys
import tempfile
import urllib.request
from collections import Counter
from pathlib import Path

from apply import describe_rules
from bucket import unleash_bucket
from cli import fs
from plan import env_var_enabled

STATE = Path(".liftover")
SOURCE_NAME = {"unleash": "Unleash", "launchdarkly": "LaunchDarkly", "env": "env var"}


def sample_users(flags, n, seed):
    """Users whose traits hit both sides of every condition in the inventory, plus users missing each trait."""
    rnd = random.Random(seed)
    values, numeric, ids = {}, set(), set()
    for f in flags.values():
        for e in f["environments"].values():
            ld = e.get("ld") or {}
            for t in ld.get("targets", []):
                ids.update(t["values"])
            for r in e.get("rules", []) + ld.get("rules", []):
                for c in r["conditions"]:
                    if c["attr"] == "userId":
                        ids.update(c["values"])
                        continue
                    values.setdefault(c["attr"], set()).update(map(str, c["values"]))
                    if c["op"].startswith("NUM_"):
                        numeric.add(c["attr"])
    users = []
    for i in range(n):
        uid = rnd.choice(sorted(ids)) if ids and rnd.random() < 0.05 else f"user-{i:05d}"
        traits = {}
        for attr, vals in sorted(values.items()):
            roll = rnd.random()
            if roll < 0.1:
                continue  # trait missing
            if attr in numeric:
                t = [float(v) for v in vals]
                traits[attr] = rnd.randint(int(min(t)) - 10, int(max(t)) + 10)
            else:
                traits[attr] = rnd.choice(sorted(vals)) if roll < 0.55 else f"other-{rnd.randint(1, 9)}"
        users.append((uid, traits))
    return users


def unleash_client(env, tmp):
    from UnleashClient import UnleashClient
    from UnleashClient.cache import FileCache
    from UnleashClient.utils import InstanceAllowType
    token = os.environ.get(f"UNLEASH_CLIENT_TOKEN_{env.upper()}")
    if not token:
        sys.exit(f"UNLEASH_CLIENT_TOKEN_{env.upper()} not set (a client token for Unleash environment {env})")
    url = os.environ["UNLEASH_URL"].rstrip("/")
    with urllib.request.urlopen(urllib.request.Request(f"{url}/client/features", headers={"Authorization": token})) as r:
        features = json.loads(r.read())
    cache = FileCache(f"liftover-{env}", directory=str(tmp))
    cache.bootstrap_from_dict(features)
    return UnleashClient(url, f"liftover-parity-{env}", api_key=token, cache=cache,
                         multiple_instance_mode=InstanceAllowType.SILENTLY_ALLOW)


class LaunchDarkly:
    """LaunchDarkly's own SDK on the same SDK data file the inventory read, offline."""

    def __init__(self, path):
        import ldclient
        from ldclient.config import Config
        from ldclient.integrations import Files
        self._ctx = ldclient.Context
        self.client = ldclient.LDClient(Config("offline-parity", send_events=False,
                                               update_processor_class=Files.new_data_source(paths=[path])))

    def variation(self, key, uid, traits):
        b = self._ctx.builder(uid)
        for k, v in traits.items():
            b.set(k, v)
        return self.client.variation(key, b.build(), None)


def flagsmith_client(project, env, tmp):
    from flagsmith import Flagsmith
    from flagsmith.offline_handlers import LocalFileHandler
    doc = Path(tmp, f"{env}.json")
    doc.write_text(json.dumps(fs("environment", "document", env, "-p", project)))
    return Flagsmith(offline_mode=True, offline_handler=LocalFileHandler(str(doc)))


def source_value(f, env, client, uid, traits):
    """-> (on, value) the way the app sees it today."""
    if f["source"] == "env":
        e = f["environments"].get(env)
        raw = e["value"] if e else None
        return (raw is not None, raw) if f["kind"] == "value" else (env_var_enabled(f, raw), None)
    if f["source"] == "launchdarkly":
        v = client[f["key"]].variation(f["key"], uid, traits) if f["key"] in client else None
        if f["kind"] == "boolean":
            return bool(v), None
        return v is not None, None if v is None else str(v)
    ctx = {"userId": uid, "properties": {k: str(v) for k, v in traits.items()}}
    on = client.is_enabled(f["key"], ctx)
    if f["kind"] != "multivariate":
        return on, None
    v = client.get_variant(f["key"], ctx)
    return on, ((v.get("payload") or {}).get("value") or v["name"]) if v.get("enabled") else None


def explain(f, src_env, plan, fs_env, traits):
    """One line on why a flag differs: the rule Flagsmith got, and the likeliest cause for the first differing user."""
    e = f["environments"].get(src_env) or {}
    used = {c["attr"] for r in e.get("rules", []) for c in r["conditions"] if c["attr"] != "userId"}
    rules = "; ".join(f"{s['name']}: {describe_rules(s['rules'][0])}" for s in plan["segments"]
                      if s.get("flag") == f["key"] and s["env"] == fs_env) or "no segment (environment default only)"
    absent = sorted(used - set(traits))
    cause = (f"this user has no {', '.join(absent)}: a negated or missing-trait rule can behave differently in the two tools"
             if absent else "every trait is present, so the rule shape differs (check AND vs OR, operator and value case)")
    return f"planned {rules}. Likely cause: {cause}"


def hashed(f, env, preserved=()):
    e = f["environments"].get(env) or {}
    ld = e.get("ld")
    if ld:
        return e["enabled"] and any(x["rollout"] for x in ld["rules"] + [ld["fallthrough"]])
    keeps_users = f.get("key") in preserved  # its rollouts use Unleash's own bucket, so they must match user for user
    return any((r["rollout_percent"] < 100 and not keeps_users) or r.get("variants") for r in e.get("rules", []))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", required=True)
    ap.add_argument("--plan", type=Path, default=STATE / "plan.json")
    ap.add_argument("--users", type=int, default=4000)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--tolerance", type=float, default=4.0, help="percentage points, for hashed flags")
    a = ap.parse_args()

    plan = json.loads(a.plan.read_text())
    flags = json.loads(Path(plan["inventory"]).read_text())["flags"]
    features = {f["name"]: f for f in plan["features"]}
    users = sample_users(flags, a.users, a.seed)
    kept = plan.get("preserved", [])  # rollouts whose users the app tells Flagsmith by trait
    kept_flags = {p["flag"] for p in kept}
    results, tmp = [], Path(tempfile.mkdtemp(dir=STATE))
    for src_env, fs_env in plan["env_map"].items():
        needs_unleash = any(flags[k]["source"] == "unleash" for k in features)
        un = unleash_client(src_env, tmp) if needs_unleash else None
        ld_files = {flags[k]["environments"][src_env]["ld_file"] for k in features
                    if flags[k]["source"] == "launchdarkly" and src_env in flags[k]["environments"]}
        lds = {path: LaunchDarkly(path) for path in ld_files}
        ld = {k: lds[flags[k]["environments"][src_env]["ld_file"]] for k in features
              if flags[k]["source"] == "launchdarkly" and src_env in flags[k]["environments"]}
        fl = flagsmith_client(a.project, fs_env, tmp)
        rows = {k: [] for k in features}
        for uid, traits in users:
            got = fl.get_identity_flags(uid, {**traits, **{p["trait"]: unleash_bucket(p["group"], uid) for p in kept}})
            for k in features:
                src = source_value(flags[k], src_env, ld if flags[k]["source"] == "launchdarkly" else un, uid, traits)
                on = got.is_feature_enabled(k)
                val = got.get_feature_value(k)
                kind = features[k].get("kind")
                if kind == "multivariate":
                    dst = (on, val if on else None)
                elif kind == "value":
                    dst = (on, None if val is None else str(val))
                else:
                    dst = (on, None)
                rows[k].append((uid, traits, src, dst))
        for k, r in rows.items():
            differ = [(u, t, s, d) for u, t, s, d in r if s != d]
            res = {"flag": k, "env": fs_env, "users": len(r), "differ": len(differ)}
            if hashed(flags[k], src_env, kept_flags):
                # Targeted ids are sampled many times with different traits; for shares count each identity once.
                once = list({row[0]: row for row in reversed(r)}.values())
                label = lambda x: x[1] if x[1] is not None else ("on" if x[0] else "off")
                share = lambda side: {label(x): round(100 * c / len(once), 1)
                                      for x, c in Counter(row[side] for row in once).items()}
                s, d = share(2), share(3)
                gap = max(abs(s.get(x, 0) - d.get(x, 0)) for x in set(s) | set(d))
                res.update(kind="statistical", source_share=s, flagsmith_share=d, gap_pp=round(gap, 1),
                           ok=gap <= a.tolerance)
            else:
                res.update(kind="exact", ok=not differ,
                           examples=[{"user": u, "traits": t, "source": s, "flagsmith": d} for u, t, s, d in differ[:3]])
                if differ:
                    res["why"] = explain(flags[k], src_env, plan, fs_env, differ[0][1])
            results.append(res)

    (STATE / "parity.json").write_text(json.dumps(results, indent=2))
    failed = [r for r in results if not r["ok"]]
    md = [f"### Parity check: {a.users} sampled users per environment, both sides evaluated by their own SDK", "",
          "| flag | environment | check | result |", "|---|---|---|---|"]
    for r in results:
        if r["kind"] == "exact":
            line = "same for every user" if r["ok"] else f"**{r['differ']} users differ**"
        else:
            on = lambda share: ", ".join(f"{k} {v:g}%" for k, v in sorted(share.items()))
            line = (f"{'within' if r['ok'] else '**outside**'} {a.tolerance:g} pts (gap {r['gap_pp']:g}); "
                    f"{SOURCE_NAME.get(flags[r['flag']]['source'], 'before')} {on(r['source_share'])} / "
                    f"Flagsmith {on(r['flagsmith_share'])}; "
                    f"{r['differ']} users switch")
        md.append(f"| {r['flag']} | {r['env']} | {r['kind']} | {line} |")
        print(f"{'PASS' if r['ok'] else 'FAIL'} {r['flag']} {r['env']} {r['kind']}: {line.replace('**', '')}")
        for ex in r.get("examples", []):
            print(f"     e.g. {ex['user']} {ex['traits']}: source {ex['source']} flagsmith {ex['flagsmith']}")
        if r.get("why"):
            print(f"     why: {r['why']}")
    md += ["", f"Not migrated: {', '.join(n['flag'] + ' (' + n['decision'] + ')' for n in plan['not_migrated']) or 'none'}"]
    (STATE / "parity.md").write_text("\n".join(md) + "\n")
    print(f"{len(results) - len(failed)}/{len(results)} pass -> .liftover/parity.md")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
