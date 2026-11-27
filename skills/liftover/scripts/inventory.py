"""Liftover step 1: inventory every flag in the source system and every flag read in the code.

Writes .liftover/inventory.json (source-neutral model, input to plan.py) and prints one line per flag.
Stdlib only.

  python inventory.py --repo PATH [--unleash] [--env-files .env.production,...]

Unleash: reads UNLEASH_URL (ending /api) and UNLEASH_ADMIN_TOKEN from the environment. Read-only.
LaunchDarkly: --launchdarkly production=ld-prod.json,... where each file is that environment's SDK data:
  curl -H "Authorization: $LD_SDK_KEY" https://sdk.launchdarkly.com/sdk/latest-all > ld-prod.json
"""
import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

CODE_EXT = {".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rb", ".java", ".kt"}
SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__", "dist", "build", ".liftover"}

# SDK calls that take a flag key as the first argument.
SDK_CALL = re.compile(
    r"""\b(?P<call>is_enabled|get_variant|isEnabled|getVariant|useFlag|useVariant|"""
    r"""variation|boolVariation|stringVariation|numberVariation|jsonVariation|variationDetail)"""
    r"""\(\s*["'](?P<key>[\w.\-]+)["']""")
# Environment-variable reads: os.environ.get("X"), os.getenv("X"), os.environ["X"], process.env.X
ENV_READ = re.compile(
    r"""os\.environ\.get\(\s*["'](?P<a>\w+)["']|os\.getenv\(\s*["'](?P<b>\w+)["']|"""
    r"""os\.environ\[\s*["'](?P<c>\w+)["']\s*\]|process\.env\.(?P<d>\w+)""")
FLAG_PREFIXES = ("FEATURE_", "FF_", "ENABLE_", "FLAG_")
# Env vars that configure the flag client itself, not the product.
CLIENT_CONFIG = re.compile(r"^(UNLEASH|LAUNCHDARKLY|LD|FLAGSMITH|APP_ENV|NODE_ENV)", re.I)


def scan_code(repo: Path):
    sdk_refs, env_refs = {}, {}
    for path in repo.rglob("*"):
        if path.suffix not in CODE_EXT or any(p in SKIP_DIRS for p in path.parts):
            continue
        rel = path.relative_to(repo).as_posix()
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            for m in SDK_CALL.finditer(line):
                sdk_refs.setdefault(m["key"], []).append(f"{rel}:{n}")
            for m in ENV_READ.finditer(line):
                name = next(g for g in m.groups() if g)
                if not CLIENT_CONFIG.match(name):
                    # Keep the line: how the code reads the value ("== 'true'", "== '1'") decides its meaning.
                    env_refs.setdefault(name, []).append(f"{rel}:{n}: {line.rstrip()[:120]}")
    return sdk_refs, env_refs


def read_env_files(repo: Path, names):
    """{env_name: {VAR: value}} from files like .env.production -> 'production'."""
    out = {}
    for name in names:
        p = repo / name
        if not p.exists():
            sys.exit(f"env file not found: {p}")
        env = name.split(".env.", 1)[1] if ".env." in name else "default"
        out[env] = dict(l.split("=", 1) for l in p.read_text().splitlines() if "=" in l and not l.startswith("#"))
    return out


def unleash_get(path):
    req = urllib.request.Request(os.environ["UNLEASH_URL"].rstrip("/") + path,
                                 headers={"Authorization": os.environ["UNLEASH_ADMIN_TOKEN"]})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def condition(cn):
    vals = cn.get("values") or ([cn["value"]] if cn.get("value") not in (None, "") else [])
    return {"attr": cn["contextName"], "op": cn["operator"], "values": vals,
            "inverted": bool(cn.get("inverted")), "case_insensitive": bool(cn.get("caseInsensitive"))}


def from_unleash():
    flags = {}
    for project in unleash_get("/admin/projects")["projects"]:
        pid = project["id"]
        for f in unleash_get(f"/admin/projects/{pid}/features")["features"]:
            d = unleash_get(f"/admin/projects/{pid}/features/{f['name']}")
            envs, variants = {}, []
            for e in d["environments"]:
                rules = []
                for s in e.get("strategies", []):
                    if s.get("disabled"):
                        continue
                    p = s.get("parameters", {})
                    rule = {"strategy": s["name"], "conditions": [condition(c) for c in s.get("constraints", [])],
                            "segments": s.get("segments", []),
                            "rollout_percent": float(p["rollout"]) if "rollout" in p else 100.0,
                            "stickiness": p.get("stickiness"), "group_id": p.get("groupId")}
                    if s.get("variants"):
                        rule["variants"] = [{"key": v["name"], "weight_pct": v["weight"] / 10,
                                             "value": (v.get("payload") or {}).get("value")} for v in s["variants"]]
                        variants = variants or rule["variants"]
                    rules.append(rule)
                envs[e["name"]] = {"enabled": e["enabled"], "rules": rules}
            flags[f["name"]] = {"key": f["name"], "source": "unleash", "project": pid,
                                "kind": "multivariate" if variants else "boolean",
                                "description": d.get("description") or "", "variants": variants,
                                "environments": envs, "source_type": d.get("type")}
    return flags


LD_OPS = {"in": "IN", "startsWith": "STR_STARTS_WITH", "endsWith": "STR_ENDS_WITH", "contains": "STR_CONTAINS",
          "matches": "MATCHES", "lessThan": "NUM_LT", "lessThanOrEqual": "NUM_LTE", "greaterThan": "NUM_GT",
          "greaterThanOrEqual": "NUM_GTE", "semVerEqual": "SEMVER_EQ", "semVerLessThan": "SEMVER_LT",
          "semVerGreaterThan": "SEMVER_GT"}  # before/after (dates) and segmentMatch stay as-is and block in plan.py


def ld_clause(c):
    """A LaunchDarkly clause in the same shape as an Unleash constraint, so plan.py maps both the same way."""
    op = LD_OPS.get(c["op"], c["op"])
    if c.get("contextKind") not in (None, "user"):
        op = f"{op} on context kind {c['contextKind']}"  # plan.py reports it as unmappable
    return {"attr": "userId" if c["attribute"] == "key" else c["attribute"], "op": op, "values": c["values"],
            "inverted": bool(c.get("negate")), "case_insensitive": False}


def from_launchdarkly(files):
    """files: {env: path} of LaunchDarkly SDK data (GET https://sdk.launchdarkly.com/sdk/latest-all)."""
    flags = {}
    for env, path in files.items():
        data = json.loads(Path(path).read_text())
        for key, f in data["flags"].items():
            if f.get("deleted"):
                continue
            var = f["variations"]
            served = lambda s: {"result": var[s["variation"]], "rollout": None} if "variation" in s else {
                "result": None, "rollout": [{"result": var[v["variation"]], "weight_pct": v["weight"] / 1000}
                                            for v in s["rollout"]["variations"]]}
            rules = [{**served(r), "conditions": [ld_clause(c) for c in r["clauses"]]} for r in f.get("rules", [])]
            ff = flags.setdefault(key, {
                "key": key, "source": "launchdarkly", "variations": var, "variants": [], "environments": {},
                "kind": "boolean" if sorted(map(repr, var)) == ["False", "True"] else "value"})
            if "rollout" in f["fallthrough"] and ff["kind"] == "value":
                ff["kind"] = "multivariate"
            ff["environments"][env] = {"enabled": f["on"], "rules": [], "ld_file": str(Path(path).resolve()), "ld": {
                "targets": [{"values": t["values"], "result": var[t["variation"]]} for t in f.get("targets", [])],
                "rules": rules, "fallthrough": served(f["fallthrough"]), "prerequisites": f.get("prerequisites", []),
                "off_result": var[f["offVariation"]] if f.get("offVariation") is not None else None}}
    return flags


def summarise_ld(e):
    if not e["enabled"]:
        return f"off -> {e['ld']['off_result']}"
    ld, parts = e["ld"], []
    parts += [f"{len(t['values'])} users -> {t['result']}" for t in ld["targets"]]
    for r in ld["rules"]:
        what = " & ".join(f"{c['attr']} {c['op']} {','.join(map(str, c['values']))[:20]}" for c in r["conditions"])
        parts.append(f"{what} -> {r['result'] if r['rollout'] is None else 'rollout'}")
    fall = ld["fallthrough"]
    parts.append("else " + (str(fall["result"]) if fall["rollout"] is None else
                            "/".join(f"{p['result']} {p['weight_pct']:g}" for p in fall["rollout"])))
    if ld["prerequisites"]:
        parts.append("needs " + ",".join(p["key"] for p in ld["prerequisites"]))
    return " | ".join(parts)


def summarise_env(e):
    if not e:
        return "-"
    if "ld" in e:
        return summarise_ld(e)
    if not e["enabled"]:
        return "off"
    if not e["rules"]:
        return "on"
    parts = []
    for r in e["rules"]:
        bits = [f"{c['attr']} {c['op']} {','.join(map(str, c['values']))[:24]}" for c in r["conditions"]]
        if r["segments"]:
            bits.append(f"segments {r['segments']}")
        if r["rollout_percent"] < 100:
            bits.append(f"{r['rollout_percent']:g}%")
        label = " & ".join(bits) or "all"
        if r.get("variants"):
            label += " [" + "/".join(f"{v['key']} {v['weight_pct']:g}" for v in r["variants"]) + "]"
        parts.append(label)
    return " | ".join(parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--unleash", action="store_true", help="read flags from Unleash")
    ap.add_argument("--launchdarkly", default="", help="env=file of LaunchDarkly SDK data, e.g. production=ld-prod.json")
    ap.add_argument("--env-files", default="", help="comma-separated, e.g. .env.production,.env.staging")
    ap.add_argument("--out", type=Path, default=Path(".liftover/inventory.json"))
    a = ap.parse_args()

    sdk_refs, env_refs = scan_code(a.repo)
    flags = from_unleash() if a.unleash else {}
    if a.launchdarkly:
        flags.update(from_launchdarkly(dict(p.split("=", 1) for p in a.launchdarkly.split(","))))

    env_values = read_env_files(a.repo, [n for n in a.env_files.split(",") if n])
    for name, refs in env_refs.items():
        values = {env: vals[name] for env, vals in env_values.items() if name in vals}
        is_flag = name.startswith(FLAG_PREFIXES)
        key = name.lower().removeprefix("feature_").removeprefix("ff_").removeprefix("enable_").removeprefix("flag_")
        flags[key] = {"key": key, "source": "env", "env_var": name,
                      "kind": "boolean" if is_flag else "value",
                      "description": f"was environment variable {name}", "variants": [],
                      "environments": {env: {"enabled": True, "value": v, "rules": []} for env, v in values.items()},
                      "needs_review": None if is_flag else "config value, not obviously a flag: migrate as remote config?"}
        flags[key]["code_refs"] = refs

    for key, f in flags.items():
        if f["source"] != "env":
            f["code_refs"] = sdk_refs.get(key, [])
    for key, refs in sdk_refs.items():
        if key not in flags:
            flags[key] = {"key": key, "source": "code-only", "kind": "unknown", "environments": {},
                          "variants": [], "code_refs": refs,
                          "needs_review": "read in code but missing from the source system (always returns the default)"}
    for f in flags.values():
        if not f["code_refs"]:
            f["needs_review"] = "not read anywhere in the code: migrate, or retire instead?"

    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(json.dumps({"repo": str(a.repo.resolve()), "flags": flags}, indent=2))

    envs = sorted({e for f in flags.values() for e in f["environments"]})
    print(f"{len(flags)} flags -> {a.out}")
    print("flag | source | kind | " + " | ".join(envs) + " | refs | review")
    for f in sorted(flags.values(), key=lambda f: (f["source"], f["key"])):
        cells = []
        for env in envs:
            e = f["environments"].get(env)
            cells.append(e["value"] if e and "value" in e else summarise_env(e))
        print(f"{f['key']} | {f['source']} | {f['kind']} | " + " | ".join(cells)
              + f" | {len(f['code_refs'])} | {f.get('needs_review') or ''}")


if __name__ == "__main__":
    main()
