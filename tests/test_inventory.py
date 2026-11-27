import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "skills/liftover/scripts/inventory.py"


class InventoryCodeScan(unittest.TestCase):
    def test_env_read_keeps_indentation(self):
        """cutover tells module-level constants from reads inside a function by the leading whitespace."""
        sys.path.insert(0, str(SCRIPT.parent))
        from inventory import scan_code
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.py").write_text('TOP = os.getenv("FEATURE_A")\ndef f():\n    return os.getenv("FEATURE_B")\n')
            refs = scan_code(Path(d))[1]
        self.assertFalse(refs["FEATURE_A"][0].split(": ", 1)[1].startswith(" "))
        self.assertTrue(refs["FEATURE_B"][0].split(": ", 1)[1].startswith(" "))

    def test_finds_sdk_env_missing_and_config(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            (repo / "app.py").write_text(
                'import os\n'
                'if client.is_enabled("new_checkout", ctx): pass\n'
                'v = client.getVariant("checkout_button")\n'
                'DARK = os.environ.get("FEATURE_DARK_MODE") == "true"\n'
                'LIMIT = os.getenv("UPLOAD_LIMIT_MB")\n'
                'URL = os.environ["UNLEASH_URL"]\n')
            (repo / "node_modules").mkdir()
            (repo / "node_modules/x.js").write_text('isEnabled("ignored_flag")')
            (repo / ".env.production").write_text("FEATURE_DARK_MODE=true\nUPLOAD_LIMIT_MB=50\n")
            out = repo / "inv.json"
            subprocess.run([sys.executable, SCRIPT, "--repo", repo, "--env-files", ".env.production",
                            "--out", out], check=True, capture_output=True)
            flags = json.loads(out.read_text())["flags"]

        self.assertEqual(set(flags), {"new_checkout", "checkout_button", "dark_mode", "upload_limit_mb"})
        self.assertEqual(flags["new_checkout"]["source"], "code-only")  # no source system given
        self.assertEqual(flags["new_checkout"]["code_refs"], ["app.py:2"])
        self.assertEqual(flags["dark_mode"]["kind"], "boolean")
        self.assertEqual(flags["dark_mode"]["environments"]["production"]["value"], "true")
        self.assertIn('== "true"', flags["dark_mode"]["code_refs"][0])
        self.assertEqual(flags["upload_limit_mb"]["kind"], "value")
        self.assertTrue(flags["upload_limit_mb"]["needs_review"])


if __name__ == "__main__":
    unittest.main()
