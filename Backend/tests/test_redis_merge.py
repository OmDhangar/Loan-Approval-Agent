import unittest

from core.redis_client import RedisClient


class RedisMergeTests(unittest.TestCase):
    def test_deep_merge_nested_keys(self):
        base = {"version": 2, "financial_data": {"verified_income": None, "foir": 0.3}}
        patch = {"financial_data": {"verified_income": 55000}}
        merged = RedisClient._deep_merge_dict(base, patch)
        self.assertEqual(merged["financial_data"]["verified_income"], 55000)
        self.assertEqual(merged["financial_data"]["foir"], 0.3)


if __name__ == "__main__":
    unittest.main()
