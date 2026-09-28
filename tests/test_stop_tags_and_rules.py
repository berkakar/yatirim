import unittest
from datetime import datetime, timezone

from rules_version import rules_fingerprint, stamp_rules_version
from stop_tags import parse_shield_real_stop, reason_code, shield_tag, stop_tag, tag_kind


class StopTagsTest(unittest.TestCase):
    def test_shield_roundtrip(self):
        tag = shield_tag("BRK.B", 494.44, now=1759059000)
        self.assertEqual(tag, "shield-BRK.B-49444-1759059000")
        self.assertAlmostEqual(parse_shield_real_stop(tag), 494.44)
        self.assertEqual(tag_kind(tag), "shield")

    def test_non_shield(self):
        self.assertIsNone(parse_shield_real_stop(None))
        self.assertIsNone(parse_shield_real_stop("algo-demand_zone-1Day-MSFT-1"))
        self.assertIsNone(tag_kind("algo-demand_zone-1Day-MSFT-1"))

    def test_reason_codes(self):
        self.assertEqual(reason_code("breakeven (+1R)"), "breakeven")
        self.assertEqual(reason_code("structure@123.40"), "structure")
        self.assertEqual(reason_code("chandelier (3xATR)"), "chandelier")
        self.assertEqual(reason_code("kâr kilidi (+%4)"), "profitlock")
        self.assertEqual(reason_code("HA çıkış sinyali - x"), "haexit")
        self.assertEqual(tag_kind(stop_tag("breakeven", "MSFT", now=1)), "breakeven")


class RulesVersionTest(unittest.TestCase):
    def test_budget_and_weights_do_not_reset_version(self):
        old = stamp_rules_version({"algorithm": "demand_zone", "budget": 1, "weights": {"A": 1}}, {},
                                  now=datetime(2026, 9, 1, tzinfo=timezone.utc))
        new = stamp_rules_version({"algorithm": "demand_zone", "budget": 2, "weights": {"A": 5}}, old,
                                  now=datetime(2026, 9, 20, tzinfo=timezone.utc))
        self.assertEqual(new["rules_version_since"], old["rules_version_since"])

    def test_rule_change_resets_version(self):
        old = stamp_rules_version({"algorithm": "demand_zone"}, {}, now=datetime(2026, 9, 1, tzinfo=timezone.utc))
        new = stamp_rules_version({"algorithm": "orb"}, old, now=datetime(2026, 9, 20, tzinfo=timezone.utc))
        self.assertTrue(new["rules_version_since"].startswith("2026-09-20"))
        self.assertNotEqual(rules_fingerprint({"algorithm": "orb"}), rules_fingerprint({"algorithm": "demand_zone"}))


if __name__ == "__main__":
    unittest.main()
