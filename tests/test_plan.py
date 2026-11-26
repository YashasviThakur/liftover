import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "skills/liftover/scripts/plan.py"


def rule(conditions=(), pct=100.0, variants=None, strategy="flexibleRollout"):
    r = {"strategy": strategy, "conditions": list(conditions), "segments": [], "rollout_percent": pct,
         "stickiness": "userId", "group_id": "g"}
    if variants:
        r["variants"] = variants
    return r


def c(attr, op, values, inverted=False, ci=False):
    return {"attr": attr, "op": op, "values": values, "inverted": inverted, "case_insensitive": ci}


def unleash(key, prod, kind="boolean", variants=()):
    return {"key": key, "source": "unleash", "kind": kind, "variants": list(variants), "code_refs": ["a.py:1"],
            "environments": {"production": prod}}


VARIANTS = [{"key": "a", "weight_pct": 70.0, "value": "A"}, {"key": "b", "weight_pct": 30.0, "value": None}]
FLAGS = {
    "or_rules": unleash("or_rules", {"enabled": True, "rules": [
        rule([c("plan", "IN", ["pro"]), c("country", "IN", ["IN", "US"])]),
        rule([c("userId", "IN", ["vip-1"])])]}),
    "negations": unleash("negations", {"enabled": True, "rules": [rule([
        c("age", "NUM_GTE", ["18"], inverted=True), c("country", "NOT_IN", ["FR", "DE"]),
        c("email", "STR_ENDS_WITH", ["@x.io"], ci=True), c("app", "SEMVER_GT", ["1.2.0"])], pct=10)]}),
    "everyone": unleash("everyone", {"enabled": True, "rules": [rule([c("plan", "IN", ["pro"])]), rule()]}),
    "variants": unleash("variants", {"enabled": True, "rules": [rule(variants=VARIANTS)]}, "multivariate", VARIANTS),
    "date_rule": unleash("date_rule", {"enabled": True, "rules": [rule([c("currentTime", "DATE_AFTER", ["2026"])])]}),
    "dark_mode": {"key": "dark_mode", "source": "env", "kind": "boolean", "variants": [],
                  "code_refs": ['a.py:3: DARK = os.getenv("FEATURE_DARK_MODE") == "1"'],
                  "environments": {"production": {"enabled": True, "value": "1", "rules": []}}},
    "dead": {**unleash("dead", {"enabled": True, "rules": []}), "code_refs": [], "needs_review": "not read"},
}


def run(*args):
    with tempfile.TemporaryDirectory() as d:
        inv, out = Path(d, "inv.json"), Path(d, "plan.json")
        inv.write_text(json.dumps({"flags": FLAGS}))
        p = subprocess.run([sys.executable, SCRIPT, "--inventory", inv, "--out", out, *args],
                           capture_output=True, text=True)
        return p, json.loads(out.read_text())


class Plan(unittest.TestCase):
    def test_mapping(self):
        p, plan = run("--retire", "dead", "--skip", "date_rule", "--env-map", "production=Production")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertTrue(plan["complete"])
        segs = {s["name"]: s for s in plan["segments"]}
        states = {s["feature"]: s for s in plan["states"]}

        # OR of strategies -> root ANY with one ALL child each (the shape issue #8721 gets wrong)
        root = segs["or_rules_production"]["rules"][0]
        self.assertEqual(root["type"], "ANY")
        self.assertEqual([len(r["conditions"]) for r in root["rules"]], [2, 1])
        self.assertEqual(root["rules"][1]["conditions"][0],
                         {"property": "$.identity.identifier", "operator": "IN", "value": "vip-1"})
        self.assertEqual(states["or_rules"]["env"], "Production")
        self.assertFalse(states["or_rules"]["enabled"])
        self.assertEqual(states["or_rules"]["segment_override"]["segment"], "or_rules_production")

        conds = segs["negations_production"]["rules"][0]["rules"][0]["conditions"]
        self.assertEqual([(x["operator"], x["value"]) for x in conds], [
            ("LESS_THAN", "18"), ("NOT_EQUAL", "FR"), ("NOT_EQUAL", "DE"),
            ("REGEX", "(?i).*(@x\\.io)$"), ("GREATER_THAN", "1.2.0:semver"), ("PERCENTAGE_SPLIT", "10")])
        self.assertIn("negations", plan["warnings"])

        self.assertTrue(states["everyone"]["enabled"])  # one strategy matches everyone: no segment
        self.assertNotIn("everyone_production", segs)

        mv = next(f for f in plan["features"] if f["name"] == "variants")
        self.assertEqual([o["value"] for o in mv["mv_options"]], ["A", "b"])
        self.assertEqual(states["variants"]["mv_pcts"], {"A": 70.0, "b": 30.0})

        self.assertTrue(states["dark_mode"]["enabled"])  # "1" == "1"
        self.assertEqual({n["flag"] for n in plan["not_migrated"]}, {"dead", "date_rule"})

    def test_undecided_and_unmappable_block(self):
        p, plan = run()
        self.assertEqual(p.returncode, 2)
        self.assertFalse(plan["complete"])
        self.assertIn("UNDECIDED dead", p.stdout)
        self.assertIn("UNDECIDED date_rule: cannot map automatically: context field 'currentTime'", p.stdout)


if __name__ == "__main__":
    unittest.main()
