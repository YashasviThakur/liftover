"""The cutover must not open a PR while parity has a failure, and parity failures must say why."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "skills/liftover/scripts"
sys.path.insert(0, str(SCRIPTS))
from parity import explain  # noqa: E402


def run_check(parity):
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        (d / ".liftover").mkdir()
        (d / "app.py").write_text("x = 1\n")
        (d / ".liftover/inventory.json").write_text(json.dumps({"flags": {}}))
        (d / ".liftover/plan.json").write_text(json.dumps({
            "inventory": str(d / ".liftover/inventory.json"), "features": [], "segments": [], "not_migrated": [],
            "environments": ["production"], "preserved": []}))
        if parity is not None:
            (d / ".liftover/parity.json").write_text(json.dumps(parity))
            (d / ".liftover/parity.md").write_text("| flag |\n")
        p = subprocess.run([sys.executable, SCRIPTS / "cutover.py", "--repo", ".", "--check"], cwd=d,
                           capture_output=True, text=True, env={**os.environ, "PYTHONPATH": str(SCRIPTS)})
        return p.returncode, p.stdout


class Gate(unittest.TestCase):
    def test_passes_only_when_parity_passed(self):
        self.assertEqual(run_check([{"flag": "a", "env": "production", "ok": True}])[0], 0)
        code, out = run_check([{"flag": "a", "env": "production", "ok": False}])
        self.assertEqual(code, 1)
        self.assertIn("parity failed: a production", out)
        code, out = run_check(None)
        self.assertEqual(code, 1)
        self.assertIn("run parity.py", out)

    def test_explain_names_the_rule_and_the_missing_trait(self):
        flag = {"key": "f", "environments": {"prod": {"rules": [{"conditions": [{"attr": "plan"}, {"attr": "userId"}]}]}}}
        plan = {"segments": [{"name": "f_prod", "flag": "f", "env": "Production", "rules": [{"rules": [
            {"conditions": [{"property": "plan", "operator": "IN", "value": "pro"}]}]}]}]}
        why = explain(flag, "prod", plan, "Production", {"age": 3})
        self.assertIn("plan IN pro", why)
        self.assertIn("no plan", why)
        self.assertIn("every trait is present", explain(flag, "prod", plan, "Production", {"plan": "pro"}))


if __name__ == "__main__":
    unittest.main()
