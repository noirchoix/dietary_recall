from __future__ import annotations

import os
import unittest

from dietary_recall.db import LegacyRepository
from dietary_recall.legacy import FoodPortion, calculate_foods


@unittest.skipUnless(os.environ.get("DIETARY_RECALL_TEST_DB"), "DIETARY_RECALL_TEST_DB not set")
class RealSnapshotGoldenCase(unittest.TestCase):
    def test_known_serialized_sunday_case(self):
        repo = LegacyRepository(os.environ["DIETARY_RECALL_TEST_DB"])
        portions = [
            FoodPortion(145, 280.0), FoodPortion(223, 30.0), FoodPortion(127, 450.0),
            FoodPortion(233, 80.0), FoodPortion(141, 350.0), FoodPortion(214, 70.0),
            FoodPortion(197, 50.0),
        ]
        result = calculate_foods(repo, portions)
        self.assertAlmostEqual(result.totals["Minerals"]["Calcium_mg"], 421.318, places=3)
        self.assertAlmostEqual(result.totals["Minerals"]["Copper_mg"], 2.0292, places=4)
        self.assertAlmostEqual(result.totals["Toxicants"]["Cadmium_mcg"], 0.569, places=3)
        self.assertAlmostEqual(result.totals["Toxicants"]["Lead_mcg"], 31.48, places=2)


if __name__ == "__main__":
    unittest.main()

