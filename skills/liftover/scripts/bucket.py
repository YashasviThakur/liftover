"""Unleash's 1-100 rollout bucket, in pure Python, so a migrated rollout can keep the same users.

Unleash puts a user on a gradual rollout when  bucket(group_id, user_id) <= rollout %.  Flagsmith hashes differently,
so `plan.py --preserve-rollouts` sends this number as the trait `unleash_bucket_<group_id>` and the rule becomes
"trait <= rollout %". Copy this file into the app and call unleash_bucket() wherever the flag is read.
"""


def _murmur3_32(data: bytes, seed: int = 0) -> int:
    c1, c2, h = 0xCC9E2D51, 0x1B873593, seed
    for i in range(0, len(data) - len(data) % 4, 4):
        k = int.from_bytes(data[i:i + 4], "little")
        k = (k * c1) & 0xFFFFFFFF
        k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
        h ^= (k * c2) & 0xFFFFFFFF
        h = ((h << 13) | (h >> 19)) & 0xFFFFFFFF
        h = (h * 5 + 0xE6546B64) & 0xFFFFFFFF
    tail = data[len(data) - len(data) % 4:]
    if tail:
        k = int.from_bytes(tail, "little")
        k = (k * c1) & 0xFFFFFFFF
        k = ((k << 15) | (k >> 17)) & 0xFFFFFFFF
        h ^= (k * c2) & 0xFFFFFFFF
    h ^= len(data)
    h ^= h >> 16
    h = (h * 0x85EBCA6B) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 0xC2B2AE35) & 0xFFFFFFFF
    return h ^ (h >> 16)


def unleash_bucket(group_id: str, user_id: str) -> int:
    """1..100, the same number Unleash's gradualRollout / flexibleRollout strategy computes."""
    return _murmur3_32(f"{group_id}:{user_id}".encode("utf-8")) % 100 + 1


VARIANT_SEED = 86028157  # Unleash hashes variants with a different seed from rollouts


def unleash_variant(flag: str, user_id: str, variants) -> str:
    """The variant Unleash serves this user: `variants` is [(name, weight), ...] in Unleash's order and weight units."""
    total = sum(w for _, w in variants)
    target = _murmur3_32(f"{flag}:{user_id}".encode("utf-8"), VARIANT_SEED) % total + 1
    seen = 0
    for name, weight in variants:
        seen += weight
        if target <= seen:
            return name
