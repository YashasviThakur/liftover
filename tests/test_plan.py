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
        self.assertEqual(states["or_rules"]["segment_overrides"],
                         [{"segment": "or_rules_production", "enabled": True, "value": None, "priority": 0}])

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

    def test_preserve_rollouts_keeps_users(self):
        p, plan = run("--retire", "dead", "--skip", "date_rule", "--preserve-rollouts")
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertEqual(plan["preserved"], [{"flag": "negations", "group": "g", "trait": "unleash_bucket_g"}])
        segs = {s["name"]: s for s in plan["segments"]}
        last = segs["negations_production"]["rules"][0]["rules"][0]["conditions"][-1]
        self.assertEqual(last, {"property": "unleash_bucket_g", "operator": "LESS_THAN_INCLUSIVE", "value": "10"})
        self.assertIn("preserved negations", p.stdout)
        _, plain = run("--retire", "dead", "--skip", "date_rule")  # off by default
        self.assertEqual(plain["preserved"], [])

    def test_undecided_and_unmappable_block(self):
        p, plan = run()
        self.assertEqual(p.returncode, 2)
        self.assertFalse(plan["complete"])
        self.assertIn("UNDECIDED dead", p.stdout)
        self.assertIn("UNDECIDED date_rule: cannot map automatically: context field 'currentTime'", p.stdout)


ROOT = Path(__file__).parents[1]


class LaunchDarklyPlan(unittest.TestCase):
    """The sandbox's LaunchDarkly SDK data, through inventory.py and plan.py."""

    def test_order_or_and_prerequisites(self):
        scripts, fx = ROOT / "skills/liftover/scripts", ROOT / "sandbox/launchdarkly"
        with tempfile.TemporaryDirectory() as d:
            inv, out = Path(d, "inv.json"), Path(d, "plan.json")
            subprocess.run([sys.executable, scripts / "inventory.py", "--repo", ROOT / "demo-app-ld", "--out", inv,
                            "--launchdarkly", f"production={fx / 'production.json'}"], check=True, capture_output=True)
            p = subprocess.run([sys.executable, scripts / "plan.py", "--inventory", inv, "--out", out],
                               capture_output=True, text=True)
            self.assertEqual(p.returncode, 2)  # legacy_search undecided, upsell_banner has a prerequisite
            self.assertIn("UNDECIDED upsell_banner: cannot map automatically: depends on prerequisite", p.stdout)
            subprocess.run([sys.executable, scripts / "plan.py", "--inventory", inv, "--out", out, "--retire",
                            "legacy_search", "--skip", "upsell_banner"], check=True, capture_output=True)
            plan = json.loads(out.read_text())
        states = {s["feature"]: s for s in plan["states"]}
        segs = {s["name"]: s for s in plan["segments"]}

        # First match wins in LaunchDarkly, so the rules keep their order as override priorities.
        tier = states["pricing_tier"]
        self.assertEqual((tier["enabled"], tier["value"]), (True, "usd"))
        self.assertEqual([(o["value"], o["priority"]) for o in tier["segment_overrides"]], [("inr", 0), ("invoice", 1)])

        # Neighbouring rules serving the same value become one OR-ed segment; the false fallthrough adds none.
        pro = states["pro_reports"]["segment_overrides"]
        self.assertEqual(len(pro), 1)
        self.assertEqual(len(segs[pro[0]["segment"]]["rules"][0]["rules"]), 2)

        # A boolean rollout becomes a percentage split; variant rollouts become multivariate weights.
        split = segs["new_checkout_production"]["rules"][0]["rules"][0]["conditions"][0]
        self.assertEqual((split["operator"], split["value"]), ("PERCENTAGE_SPLIT", "25"))
        self.assertEqual(states["checkout_button"]["mv_pcts"], {"buy_now": 50.0, "get_it_today": 30.0, "add_to_bag": 20.0})


if __name__ == "__main__":
    unittest.main()
