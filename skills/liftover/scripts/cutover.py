"""Liftover step 5: the code changes, as a worklist, and a check that they are done.

  python cutover.py --repo PATH            # worklist: every line to change and what it becomes
  python cutover.py --repo PATH --check    # exit 1 while any migrated flag is still read from the old system

Reads .liftover/plan.json and the inventory. The agent makes the edits; this script only points and verifies,
so the agent reads the listed lines instead of the whole codebase.
"""
import argparse
import json
import re
import sys
from pathlib import Path

from inventory import CODE_EXT, SKIP_DIRS, scan_code

STATE = Path(".liftover")
OLD_CLIENT = re.compile(r"\b(UnleashClient|unleash-client|unleash-proxy-client|@unleash/|ldclient|launchdarkly)", re.I)
FLAGSMITH_CALL = re.compile(r"""\b(is_feature_enabled|get_feature_value|hasFeature|getValue)\(\s*["'](?P<key>[\w.\-]+)["']""")

PY = {"boolean": 'flags.is_feature_enabled("{k}")', "multivariate": 'flags.get_feature_value("{k}")',
      "value": 'flags.get_feature_value("{k}")'}
JS = {"boolean": 'flags.hasFeature("{k}")', "multivariate": 'flags.getValue("{k}")', "value": 'flags.getValue("{k}")'}


def files(repo):
    for p in repo.rglob("*"):
        if p.suffix in CODE_EXT and not any(x in SKIP_DIRS for x in p.parts):
            yield p


def write_pr_body(plan, migrated):
    parity = STATE / "parity.md"
    if not parity.exists():
        sys.exit("no .liftover/parity.md: run parity.py (step 4) before opening the PR")
    sources = sorted({f["source"] for f in migrated.values()})
    body = [f"## Move feature flags to Flagsmith (from {', '.join(sources)})", "",
            f"- {len(plan['features'])} flags now read from Flagsmith, {len(plan['segments'])} targeting segments, "
            f"environments: {', '.join(plan['environments'])}. Everything created is tagged `migrated`.",
            "- Unknown flags still answer off (default_flag_handler), as before.",
            "- Percentage rollouts and variants keep their shares, but Flagsmith hashes users differently, "
            "so some users switch bucket once (counted below)." if not plan.get("preserved") else
            "- Rollouts keep their exact users: the app sends Unleash's bucket number as a trait "
            f"({', '.join(p['trait'] for p in plan['preserved'])}), so nobody switches. Variants keep their shares only.",
            ""]
    body += [l.rstrip("\n") for l in parity.read_text().splitlines()]
    body += ["", "### Rollback",
             "Revert this PR. To also remove what was created in Flagsmith: `python apply.py --rollback --execute`.",
             "Keep the old system running until this has been live for a while; retire it in a later change."]
    (STATE / "pr.md").write_text("\n".join(body) + "\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--plan", type=Path, default=STATE / "plan.json")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    plan = json.loads(a.plan.read_text())
    inv = json.loads(Path(plan["inventory"]).read_text())["flags"]
    migrated = {f["name"]: inv[f["name"]] for f in plan["features"]}
    retired = {n["flag"] for n in plan["not_migrated"] if n["decision"] == "retire"}

    sdk_refs, env_refs = scan_code(a.repo)
    old = [f"{k}: {ref}" for k, refs in sdk_refs.items() if k in migrated or k in retired for ref in refs]
    old += [f"{f['env_var']}: {ref.split(': ', 1)[0]}" for f in migrated.values() if f["source"] == "env"
            for ref in env_refs.get(f["env_var"], [])]
    clients = [f"{p.relative_to(a.repo).as_posix()}:{n}" for p in files(a.repo)
               for n, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
               if OLD_CLIENT.search(line)]
    new = {m["key"] for p in files(a.repo) for m in FLAGSMITH_CALL.finditer(p.read_text(encoding="utf-8", errors="replace"))}

    if a.check:
        read_before = [k for k, f in migrated.items() if f["code_refs"]]
        missing = [k for k in read_before if k not in new]
        for line in old:
            print(f"still old  {line}")
        for line in clients:
            print(f"old client {line}")
        for k in missing:
            print(f"not read from Flagsmith: {k}")
        text = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files(a.repo))
        no_trait = [p["trait"] for p in plan.get("preserved", []) if "unleash_bucket(" not in text]
        for t in no_trait:
            print(f"bucket trait not sent: {t} (copy scripts/bucket.py into the app, pass unleash_bucket(group, user_id))")
        ok = not (old or clients or missing or no_trait)
        if not ok:
            print(f"cutover incomplete: {len(old) + len(clients) + len(missing) + len(no_trait)} items")
            sys.exit(1)
        write_pr_body(plan, migrated)
        print("cutover complete: every migrated flag is read from Flagsmith, nothing reads the old system "
              "-> .liftover/pr.md")
        return

    print("Replace the old client with one Flagsmith client (server-side key, FLAGSMITH_ENVIRONMENT_KEY):")
    print("  python: from flagsmith import Flagsmith; from flagsmith.models import DefaultFlag")
    print("          flagsmith = Flagsmith(environment_key=os.environ['FLAGSMITH_ENVIRONMENT_KEY'],")
    print("              default_flag_handler=lambda name: DefaultFlag(enabled=False, value=None))"
          "  # unknown flag -> off, like the old SDK; without it the Python SDK raises")
    print("          flags = flagsmith.get_identity_flags(user_id, traits={...same attributes as the old context...})")
    print("          no user in scope (a job, a startup check): flagsmith.get_environment_flags() instead, same methods")
    print("  js:     flagsmith.init({ environmentID }); identify(userId, traits)")
    for p in plan.get("preserved", []):
        print(f"bucket   {p['flag']}: copy scripts/bucket.py into the app; wherever the user is identified pass trait "
              f"{p['trait']}=unleash_bucket({p['group']!r}, user_id) so the rollout keeps the same users")
    for ref in clients:
        print(f"client   {ref}")
    for k, f in sorted(migrated.items()):
        kind = "value" if f["source"] == "env" and f["kind"] == "value" else f["kind"]
        for ref in (f["code_refs"] if f["source"] != "env" else []):
            call = (JS if ref.split(":")[0].endswith((".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs")) else PY)[kind]
            extra = {"multivariate": " (value is the variant value; check flags.is_feature_enabled first)",
                     "value": " (keep the old call's default for when it returns None)"}.get(kind, "")
            print(f"flag     {ref}  {k} -> {call.format(k=k)}{extra}")
        if f["source"] == "env":
            for ref in f["code_refs"]:
                where, line = ref.split(": ", 1)
                hint = ("keep the code's default when the value is None" if kind == "value"
                        else "the Flagsmith flag already holds the result of this comparison")
                if line[:1] not in (" ", "	"):  # read once at import: it would freeze one answer for every user
                    hint += "; module-level constant: move the read into the function that has the user"
                print(f"env      {where}  {f['env_var']} -> {PY[kind].format(k=k)}  ({hint})  was: {line.strip()}")
    for k in sorted(retired):
        for ref in inv[k]["code_refs"]:
            print(f"retire   {ref}  {k}: delete the check and keep the branch users get today")
    for n in plan["not_migrated"]:
        if n["decision"] == "skip":
            src = inv[n["flag"]]["source"]
            for ref in inv[n["flag"]]["code_refs"]:
                if src == "code-only":
                    print(f"skipped  {ref}  {n['flag']}: not in Flagsmith. Read it through Flagsmith anyway "
                          f"(with the default_flag_handler above a missing flag is off, as it is today) "
                          f"unless the user says to delete the check")
                else:
                    print(f"STOP     {ref}  {n['flag']}: still served by {src} and not migrated. After cutover it is "
                          f"off for everyone unless it is rebuilt in Flagsmith first. Ask the user before changing this line")


if __name__ == "__main__":
    main()
