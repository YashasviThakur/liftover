"""bucket.py must put exactly the users on a rollout that the real Unleash SDK puts there."""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/liftover/scripts"))
from bucket import _murmur3_32, unleash_bucket  # noqa: E402


class Bucket(unittest.TestCase):
    def test_murmur3_reference_vectors(self):
        self.assertEqual(_murmur3_32(b""), 0)
        self.assertEqual(_murmur3_32(b"", 1), 0x514E28B7)
        self.assertEqual(_murmur3_32(b"test"), 0xBA6BD213)
        self.assertEqual(_murmur3_32(b"Hello, world!", 0x9747B28C), 0x24884CBA)

    def test_matches_unleash_sdk(self):
        try:
            from UnleashClient import UnleashClient
            from UnleashClient.cache import FileCache
            from UnleashClient.utils import InstanceAllowType
        except ImportError:
            self.skipTest("UnleashClient not installed")
        feature = {"name": "roll", "type": "release", "enabled": True, "project": "default", "stale": False,
                   "strategies": [{"name": "flexibleRollout", "constraints": [], "segments": [], "variants": [],
                                   "parameters": {"rollout": "37", "stickiness": "default", "groupId": "grp"}}],
                   "variants": [], "impressionData": False}
        with tempfile.TemporaryDirectory() as d:
            cache = FileCache("bucket-test", directory=d)
            cache.bootstrap_from_dict({"version": 2, "features": [feature]})
            client = UnleashClient("http://127.0.0.1:1/api", "bucket-test", cache=cache,
                                   multiple_instance_mode=InstanceAllowType.SILENTLY_ALLOW)
            ids = [f"user-{i:05d}" for i in range(2000)]
            wrong = [u for u in ids if client.is_enabled("roll", {"userId": u}) != (unleash_bucket("grp", u) <= 37)]
        self.assertEqual(wrong, [], f"{len(wrong)} of {len(ids)} users differ, e.g. {wrong[:3]}")


if __name__ == "__main__":
    unittest.main()
