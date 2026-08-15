from __future__ import annotations

import unittest

from dietary_recall.legacy import (
    FoodPortion,
    activity_multiplier,
    jdbc_float,
    legacy_eaten_recommendation_vector,
    mean_vectors,
    scale_composition,
)


class FakeCalculation:
    totals = {
        "Basic_Components": {
            "Carbohydrates_g": 10.0,
            "Dietary_Fibre_g": 3.0,
            "Soluble_Fibre_g": 2.0,
            "Fat_g": 4.0,
            "Protein_g": 5.0,
        },
        "Vitamins": {
            "Vitamin_A_IU_IU": 100.0,
            "Vitamin_C_mg": 20.0,
            "Vitamin_D_mcg_mcg": 2.0,
            "Vitamin_E_mg": 1.0,
            "Vitamin_K_mcg": 3.0,
            "Niacin_mg": 4.0,
            "Vitamin_B6_mg": 5.0,
            "Folate_mcg": 6.0,
            "Vitamin_B12_mcg": 7.0,
            "Panthotenic_Acid_mg": 8.0,
            "Biotin_mcg": 9.0,
        },
        "Minerals": {
            "Calcium_mg": 1.0,
            "Chromium_mcg": 2.0,
            "Copper_mg": 0.003,
            "Fluoride_mg": 4.0,
            "Iodine_mcg": 5.0,
            "Iron_mg": 6.0,
            "Magnesium_mg": 7.0,
            "Manganese_mg": 8.0,
            "Molybdenum_mcg": 9.0,
            "Phosphorus_mg": 10.0,
            "Selenium_mcg": 11.0,
            "Zinc_mg": 12.0,
        },
    }


class LegacyCompatibilityTests(unittest.TestCase):
    def test_jdbc_null_is_zero(self):
        self.assertEqual(jdbc_float(None), 0.0)

    def test_100g_scaling(self):
        result = scale_composition({"Calcium_mg": 50.0}, ["Calcium_mg"], 250.0)
        self.assertEqual(result["Calcium_mg"], 125.0)

    def test_legacy_activity_multipliers(self):
        self.assertEqual(activity_multiplier(1), 0.75)
        self.assertEqual(activity_multiplier(3), 1.0)
        self.assertEqual(activity_multiplier(5), 1.5)

    def test_known_recommendation_quirks_are_explicit(self):
        eaten = legacy_eaten_recommendation_vector(FakeCalculation())
        self.assertEqual(eaten["macro"]["Total_Fibre_g"], 5.0)
        self.assertEqual(eaten["vitamin"]["Vitamin_A_mcg"], 100.0)
        self.assertEqual(eaten["vitamin"]["Thiamin_mg"], 0.0)
        self.assertEqual(eaten["vitamin"]["Riboflavin_mg"], 0.0)
        self.assertEqual(eaten["vitamin"]["Choline_mg"], 0.0)
        self.assertEqual(eaten["mineral"]["Copper_mcg"], 3.0)

    def test_average_is_over_recorded_rows(self):
        self.assertEqual(mean_vectors([[2, 4], [4, 8]]), [3.0, 6.0])

    def test_mismatched_vectors_are_rejected(self):
        with self.assertRaises(ValueError):
            mean_vectors([[1, 2], [1]])


if __name__ == "__main__":
    unittest.main()

