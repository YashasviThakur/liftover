"""Liftover step 3: apply .liftover/plan.json to Flagsmith through the Flagsmith CLI.

  python apply.py --project NAME                                # dry run: print the change list, write nothing
  python apply.py --project NAME --execute                      # apply non-production changes
  python apply.py --project NAME --execute --confirm-production TOKEN   # also production (TOKEN from the dry run)
  python apply.py --rollback [--execute]                        # undo what liftover created

Idempotent: reads what exists first and only lists what differs, so a rerun after a failure picks up where it stopped.
Everything created is recorded in .liftover/applied.json (for --rollback) and features get the tag "migrated".
An environment counts as production when its name contains "prod" or it is listed in --production-envs.
Needs FLAGSMITH_API_URL, an admin key (FLAGSMITH_API_KEY or FLAGSMITH_API_KEY_<host>) and FLAGSMITH_ORGANISATION.
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from cli import fs

STATE = Path(".liftover")
APPLIED = STATE / "applied.json"
TAG = "migrated"


def norm_rule(r):
    """Compare rule trees regardless of ids, $schema and the API's 'property_' spelling."""
    return {"type": r["type"],
            "conditions": sorted((c["operator"], c.get("property") or c.get("property_") or "", str(c.get("value")))
                                 for c in r.get("conditions") or []),
            "rules": [norm_rule(x) for x in r.get("rules") or []]}


def record(key, value):
    data = json.loads(APPLIED.read_text()) if APPLIED.exists() else {}
    if key.endswith("s_created"):  # environments_created, features_created, segments_created
        data.setdefault(key, []).append(value)
    else:
        data[key] = value
    APPLIED.write_text(json.dumps(data, indent=2))


def tmp_json(name, obj):
    p = STATE / "tmp" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj))
    return f"@{p}"


class Ctx:
    """Current Flagsmith state, read once; refreshed lazily as things get created."""

    def __init__(self, project):
        self.project = project
        self.pid = next((p["id"] for p in fs("project", "list") or [] if p["name"] == project), None)
        self.envs, self.features, self.segments = {}, {}, {}
        if self.pid:
            self.refresh()

    def refresh(self):
        p = ["-p", self.pid]
        self.envs = {e["name"]: e for e in fs("environment", "list", *p) or []}
        self.features = {f["name"]: f for f in fs("api", f"api/v1/projects/{self.pid}/features/?page_size=999")["results"]}
        self.segments = {s["name"]: s for s in fs("api", f"api/v1/projects/{self.pid}/segments/?page_size=999&include_feature_specific=true")["results"]}
        tags = fs("api", f"api/v1/projects/{self.pid}/tags/")
        self.tag_id = next((t["id"] for t in (tags["results"] if isinstance(tags, dict) else tags) if t["label"] == TAG), None)

    def flag(self, feature, env):
        return fs("flag", "get", feature, "-p", self.pid, env_key=self.envs[env]["api_key"], check=False)

    def overrides(self, feature, env):
        """{segment name: (priority, enabled, value)}; the CLI only reports how many overrides there are."""
        q = f"environment={self.envs[env]['id']}&feature={self.features[feature]['id']}"
        segs = {s["id"]: s for s in fs("api", f"api/v1/features/feature-segments/?{q}")["results"]}
        out = {}
        for st in fs("api", f"api/v1/features/featurestates/?{q}")["results"]:
            fsg = segs.get(st.get("feature_segment"))
            if fsg:
                v = st["feature_state_value"] or {}
                val = next((v[k] for k in ("string_value", "integer_value", "boolean_value") if v.get(k) is not None), None)
                out[fsg["segment_name"]] = (fsg["priority"], st["enabled"], None if val is None else str(val))
        return out


def build_actions(plan, ctx, production):
    """-> list of (env or None, is_production, description, run)."""
    acts = []
    add = lambda env, text, run: acts.append((env, bool(env) and production(env), text, run))
    P = lambda: ["-p", ctx.pid]

    if not ctx.pid:
        def create_project():
            ctx.pid = fs("project", "create", ctx.project)["id"]
            record("project_created", ctx.project)
        add(None, f"create project {ctx.project}", create_project)

    def ensure_tag():
        tags = fs("api", f"api/v1/projects/{ctx.pid}/tags/")
        tags = tags["results"] if isinstance(tags, dict) else tags
        found = next((t for t in tags if t["label"] == TAG), None)
        return found["id"] if found else fs("api", f"api/v1/projects/{ctx.pid}/tags/", "-f", f"label={TAG}",
                                             "-f", "color=#6837FC")["id"]

    def tag_feature(feature):
        body = tmp_json("tag.json", {"tags": sorted(set(feature.get("tags") or []) | {ensure_tag()})})
        fs("api", f"api/v1/projects/{ctx.pid}/features/{feature['id']}/", "-X", "PATCH", "--input", body[1:])

    created_before = set(json.loads(APPLIED.read_text()).get("features_created", [])) if APPLIED.exists() else set()

    for env in plan["environments"]:
        if env not in ctx.envs:
            def create_env(env=env):
                fs("environment", "create", env, *P())
                record("environments_created", env)
                ctx.refresh()
            add(None, f"create environment {env}", create_env)

    for f in plan["features"]:
        existing = ctx.features.get(f["name"])
        if existing and f["type"].lower() != existing["type"].lower():
            sys.exit(f"feature {f['name']} exists as {existing['type']}, plan says {f['type']}: resolve by hand")
        if not existing:
            def create_feature(f=f):
                args = ["feature", "create", f["name"], *P(), "--description", f["description"]]
                if f["type"] == "MULTIVARIATE":
                    first = next(s for s in plan["states"] if s["feature"] == f["name"] and s.get("mv_pcts"))
                    args += ["--value", f["initial_value"], "--variants", tmp_json(
                        f"{f['name']}.variants.json",
                        [{"value": o["value"], "weight": first["mv_pcts"].get(o["value"], 0)} for o in f["mv_options"]])]
                created = fs(*args)
                record("features_created", f["name"])
                tag_feature(created)
                ctx.refresh()
            kind = f"multivariate {'/'.join(o['value'] for o in f['mv_options'])}" if f["mv_options"] else "flag"
            add(None, f"create feature {f['name']} ({kind}, tagged {TAG})", create_feature)
        elif f["name"] in created_before and ctx.tag_id not in (existing.get("tags") or []):
            add(None, f"tag feature {f['name']} {TAG}", lambda existing=existing: tag_feature(existing))

    for s in plan["segments"]:
        cur = ctx.segments.get(s["name"])
        if not cur:
            def create_segment(s=s):
                fs("segment", "create", s["name"], *P(), "--feature", s["flag"],
                   "--rules", tmp_json(f"{s['name']}.rules.json", s["rules"][0]),
                   "--description", f"liftover: {s['flag']} in {s['env']}")
                record("segments_created", s["name"])
                ctx.refresh()
            add(s["env"], f"create segment {s['name']} for {s['flag']}: {describe_rules(s['rules'][0])}", create_segment)
        elif [norm_rule(r) for r in cur["rules"]] != [norm_rule(r) for r in s["rules"]]:
            def update_segment(s=s):
                fs("segment", "update", s["name"], *P(), "--rules", tmp_json(f"{s['name']}.rules.json", s["rules"][0]), "--yes")
            add(s["env"], f"update segment {s['name']} rules: {describe_rules(s['rules'][0])}", update_segment)

    for st in plan["states"]:
        env, name = st["env"], st["feature"]
        if name in ctx.features and env in ctx.envs:
            cur = ctx.flag(name, env)
        else:  # what a feature looks like right after create_feature
            f = next(x for x in plan["features"] if x["name"] == name)
            first = next((s for s in plan["states"] if s["feature"] == name and s.get("mv_pcts")), {})
            cur = {"enabled": False, "value": f["initial_value"], "segment_overrides": 0,
                   "variants": [{"value": k, "weight": v} for k, v in first.get("mv_pcts", {}).items()]}
        changes, desc = [], []
        if not cur or cur.get("enabled") != st["enabled"]:
            changes.append("--enable" if st["enabled"] else "--disable")
            desc.append("on" if st["enabled"] else "off")
        if st.get("value") is not None and (not cur or str(cur.get("value")) != str(st["value"])):
            changes += ["--value", st["value"], "--type", "string"]
            desc.append(f"value {st['value']!r}")
        if st.get("mv_pcts"):
            weights = {v["value"]: v["weight"] for v in (cur or {}).get("variants") or []}
            if weights != st["mv_pcts"]:
                desc.append("weights " + "/".join(f"{k} {v:g}" for k, v in st["mv_pcts"].items()))
                changes.append(("weights", st["mv_pcts"]))
        if changes:
            def set_state(name=name, env=env, changes=changes):
                args = [c for c in changes if not isinstance(c, tuple)]
                for c in changes:
                    if isinstance(c, tuple):
                        ids = {v["value"]: v["id"] for v in fs("feature", "variant", "list", name, "-p", ctx.pid)}
                        args += ["--weight", ",".join(f"{ids[k]}={v:g}" for k, v in c[1].items())]
                fs("flag", "update", name, "-p", ctx.pid, *args, "--yes", env_key=ctx.envs[env]["api_key"])
            add(env, f"{name} in {env}: default " + ", ".join(desc), set_state)
        have = ctx.overrides(name, env) if name in ctx.features and env in ctx.envs else {}
        for ov in st.get("segment_overrides", []):
            want = (ov["priority"], ov["enabled"], None if ov["value"] is None else str(ov["value"]))
            if have.get(ov["segment"]) != want:
                def set_override(name=name, env=env, ov=ov):
                    args = ["--enable" if ov["enabled"] else "--disable", "--priority", ov["priority"]]
                    if ov["value"] is not None:
                        args += ["--value", ov["value"], "--type", "string"]
                    fs("flag", "update", name, "-p", ctx.pid, "--segment", ov["segment"], *args, "--yes",
                       env_key=ctx.envs[env]["api_key"])
                served = ov["value"] if ov["value"] is not None else ("on" if ov["enabled"] else "off")
                add(env, f"{name} in {env}: {served} for segment {ov['segment']} (priority {ov['priority']})",
                    set_override)
    return acts


def describe_rules(root):
    def cond(c):
        return f"{c['property'].replace('$.identity.identifier', 'user').replace('$.identity.key', 'user')} {c['operator']} {c['value']}"
    return " OR ".join("(" + " AND ".join(cond(c) for c in r["conditions"]) + ")" for r in root["rules"])


def token(lines):
    return hashlib.sha256("\n".join(lines).encode()).hexdigest()[:8]


def rollback(execute):
    if not APPLIED.exists():
        sys.exit("nothing to roll back: .liftover/applied.json not found")
    a = json.loads(APPLIED.read_text())
    project = a.get("project")
    if a.get("project_created"):
        steps = [(f"delete project {project} (created by liftover)", ["project", "delete", project, "--yes"])]
    else:
        p = ["-p", project]
        steps = ([(f"delete feature {f}", ["feature", "delete", f, *p, "--yes"]) for f in a.get("features_created", [])]
                 + [(f"delete segment {s}", ["segment", "delete", s, *p, "--yes"]) for s in a.get("segments_created", [])]
                 + [(f"delete environment {e}", ["environment", "delete", e, *p, "--yes"]) for e in a.get("environments_created", [])])
    for text, _ in steps:
        print(("" if execute else "would ") + text)
    if execute:
        for _, args in steps:
            fs(*args, check=False)  # a feature delete may already have removed its segments
        APPLIED.rename(APPLIED.with_suffix(".rolled-back.json"))
        print(f"rolled back {len(steps)} steps")
    else:
        print("dry run: add --execute to roll back")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", type=Path, default=STATE / "plan.json")
    ap.add_argument("--project")
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--confirm-production", default="")
    ap.add_argument("--production-envs", default="", help="comma-separated, in addition to names containing 'prod'")
    ap.add_argument("--rollback", action="store_true")
    a = ap.parse_args()
    if a.rollback:
        return rollback(a.execute)
    if not a.project:
        sys.exit("--project is required")
    plan = json.loads(a.plan.read_text())
    if not plan.get("complete"):
        sys.exit("plan is incomplete (UNDECIDED rows): rerun plan.py with the decisions first")
    extra = set(filter(None, a.production_envs.split(",")))
    production = lambda env: env in extra or re.search("prod", env, re.I) is not None

    ctx = Ctx(a.project)
    acts = build_actions(plan, ctx, production)
    prod_lines = [t for _, is_prod, t, _ in acts if is_prod]
    tok = token(prod_lines)
    if not acts:
        print(f"nothing to do: Flagsmith project {a.project} already matches the plan")
        return
    print(f"{len(acts)} changes to Flagsmith project {a.project} ({len(prod_lines)} in production)")
    for _, is_prod, text, _ in acts:
        print(("PROD " if is_prod else "     ") + text)

    if not a.execute:
        print("dry run: nothing written. Add --execute to apply non-production changes"
              + (f"; production needs --confirm-production {tok} after the user approves the PROD lines" if prod_lines else ""))
        return
    allow_prod = bool(prod_lines) and a.confirm_production == tok
    if prod_lines and a.confirm_production and not allow_prod:
        sys.exit("--confirm-production does not match this change list (it changed since the dry run): dry-run again")
    APPLIED.parent.mkdir(parents=True, exist_ok=True)
    if not APPLIED.exists():
        record("project", a.project)
    done = held = 0
    for env, is_prod, text, run in acts:
        if is_prod and not allow_prod:
            held += 1
            continue
        run()
        done += 1
        print(f"ok   {text}")
    print(f"applied {done} changes" + (f"; held {held} production changes (need --confirm-production {tok})" if held else ""))


if __name__ == "__main__":
    main()
