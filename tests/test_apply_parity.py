import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/liftover/scripts"))
from apply import describe_rules, norm_rule, token  # noqa: E402
from parity import hashed, sample_users  # noqa: E402

PLANNED = {"type": "ANY", "conditions": [], "rules": [
    {"type": "ALL", "rules": [], "conditions": [{"property": "plan", "operator": "IN", "value": "pro"},
                                                {"property": "$.identity.identifier", "operator": "IN", "value": "vip-1"}]}]}
# The same tree as Flagsmith returns it: ids, $schema, 'property_' and conditions in another order.
STORED = {"id": 9, "$schema": "x", "type": "ANY", "rules": [
    {"id": 10, "type": "ALL", "conditions": [{"id": 2, "operator": "IN", "property_": "$.identity.identifier", "value": "vip-1"},
                                             {"id": 1, "operator": "IN", "property_": "plan", "value": "pro"}]}]}


class Apply(unittest.TestCase):
    def test_rule_comparison_ignores_api_noise_but_not_logic(self):
        self.assertEqual(norm_rule(PLANNED), norm_rule(STORED))
        anded = {**STORED, "type": "ALL"}  # the importer bug #8721 shape
        self.assertNotEqual(norm_rule(PLANNED), norm_rule(anded))

    def test_confirmation_token_is_tied_to_the_exact_change_list(self):
        lines = ["create segment a", "a in production: on"]
        self.assertEqual(token(lines), token(list(lines)))
        self.assertNotEqual(token(lines), token(lines + ["b in production: on"]))

    def test_describe(self):
        self.assertEqual(describe_rules(PLANNED), "(plan IN pro AND user IN vip-1)")


class Parity(unittest.TestCase):
    FLAGS = {"f": {"environments": {"production": {"rules": [
        {"conditions": [{"attr": "age", "op": "NUM_GTE", "values": ["18"]},
                        {"attr": "plan", "op": "IN", "values": ["pro"]},
                        {"attr": "userId", "op": "IN", "values": ["vip-1"]}],
         "rollout_percent": 100.0}]}}}}

    def test_samples_cover_both_sides_and_missing_traits(self):
        users = sample_users(self.FLAGS, 500, 1)
        ages = [t["age"] for _, t in users if "age" in t]
        self.assertTrue(min(ages) < 18 <= max(ages))
        plans = {t.get("plan") for _, t in users}
        self.assertTrue({"pro", None} <= plans and len(plans) > 2)
        self.assertIn("vip-1", {u for u, _ in users})
        self.assertEqual(users, sample_users(self.FLAGS, 500, 1))  # reproducible

    def test_hashed(self):
        self.assertFalse(hashed(self.FLAGS["f"], "production"))
        rolled = {"environments": {"production": {"rules": [{"rollout_percent": 25.0}]}}}
        self.assertTrue(hashed(rolled, "production"))


if __name__ == "__main__":
    unittest.main()
