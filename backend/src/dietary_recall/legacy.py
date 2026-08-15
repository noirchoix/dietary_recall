"""Explicit reimplementation of the recoverable Java nutrient behavior."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .constants import (
    ACTIVITY_MULTIPLIERS,
    BASIC_FIELDS,
    DRI_FIELDS,
    MINERAL_FIELDS,
    OTHER_FIELDS,
    POLY_FAT_FIELDS,
    REFERENCE_FOOD_WEIGHT_G,
    TABLE_FIELDS,
    TOXICANT_FIELDS,
    VITAMIN_FIELDS,
)
from .db import LegacyRepository


@dataclass(frozen=True)
class FoodPortion:
    food_id: int
    grams: float


@dataclass(frozen=True)
class LegacyCalculation:
    portions: tuple[FoodPortion, ...]
    totals: dict[str, dict[str, float]]


def jdbc_float(value: Any) -> float:
    """Match ResultSet.getFloat(): SQL NULL is observed as 0 without wasNull()."""
    if value is None or value == "":
        return 0.0
    return float(value)


def scale_composition(row: Mapping[str, Any] | None, fields: Iterable[str], grams: float) -> dict[str, float]:
    factor = float(grams) / REFERENCE_FOOD_WEIGHT_G
    if row is None:
        return {field: 0.0 for field in fields}
    return {field: jdbc_float(row.get(field)) * factor for field in fields}


def calculate_foods(repository: LegacyRepository, portions: Iterable[FoodPortion]) -> LegacyCalculation:
    frozen = tuple(portions)
    totals = {table: {field: 0.0 for field in fields} for table, fields in TABLE_FIELDS.items()}
    for portion in frozen:
        if portion.grams < 0:
            raise ValueError("Food weight cannot be negative")
        food = repository.food_row(portion.food_id)
        food_name = str(food["Food_Name"])
        for table, fields in TABLE_FIELDS.items():
            row = repository.composition_row(table, portion.food_id, food_name)
            values = scale_composition(row, fields, portion.grams)
            for field, value in values.items():
                totals[table][field] += value
    return LegacyCalculation(portions=frozen, totals=totals)


def legacy_eaten_recommendation_vector(calculation: LegacyCalculation) -> dict[str, dict[str, float]]:
    """Return the exact nutrient selections used by RecommendedDailyProcess.

    This intentionally preserves known Java semantics such as Vitamin A using
    the IU field, thiamin/riboflavin/choline being hard-coded to zero, and total
    fibre being dietary + soluble fibre.
    """
    b = calculation.totals["Basic_Components"]
    v = calculation.totals["Vitamins"]
    m = calculation.totals["Minerals"]
    return {
        "macro": {
            "Carbohydrate_g": b["Carbohydrates_g"],
            "Total_Fibre_g": b["Dietary_Fibre_g"] + b["Soluble_Fibre_g"],
            "Fat_g": b["Fat_g"],
            "Protein_g": b["Protein_g"],
        },
        "vitamin": {
            "Vitamin_A_mcg": v["Vitamin_A_IU_IU"],
            "Vitamin_C_mg": v["Vitamin_C_mg"],
            "Vitamin_D_mcg": v["Vitamin_D_mcg_mcg"],
            "Vitamin_E_mg": v["Vitamin_E_mg"],
            "Vitamin_K_mcg": v["Vitamin_K_mcg"],
            "Thiamin_mg": 0.0,
            "Riboflavin_mg": 0.0,
            "Niacin_mg": v["Niacin_mg"],
            "Vitamin_B6_mg": v["Vitamin_B6_mg"],
            "Folate_mcg": v["Folate_mcg"],
            "Vitamin_B12_mcg": v["Vitamin_B12_mcg"],
            "Pantothenic_Acid_mg": v["Panthotenic_Acid_mg"],
            "Biotin_mcg": v["Biotin_mcg"],
            "Choline_mg": 0.0,
        },
        "mineral": {
            "Calcium_mg": m["Calcium_mg"],
            "Chromium_mcg": m["Chromium_mcg"],
            # Java chart code later special-cases copper unit conversion.
            "Copper_mcg": m["Copper_mg"] * 1000.0,
            "Flouride_mg": m["Fluoride_mg"],
            "Iodine_mcg": m["Iodine_mcg"],
            "Iron_mg": m["Iron_mg"],
            "Magnesium_mg": m["Magnesium_mg"],
            "Manganese_mg": m["Manganese_mg"],
            "Molybdenum_mcg": m["Molybdenum_mcg"],
            "Phosphorus_mg": m["Phosphorus_mg"],
            "Selenium_mcg": m["Selenium_mcg"],
            "Zinc_mg": m["Zinc_mg"],
        },
    }


def activity_multiplier(activity_level: int | str) -> float:
    try:
        return ACTIVITY_MULTIPLIERS[activity_level]
    except KeyError as exc:
        raise ValueError(f"Unknown legacy activity level: {activity_level!r}") from exc


def legacy_recommendations(
    repository: LegacyRepository,
    stage_id: int,
    life_id: int,
    activity_level: int | str = 3,
) -> dict[str, dict[str, float]]:
    """Read DRI rows and apply the Java activity multiplier to every value."""
    rows = repository.dri_rows(stage_id, life_id)
    multiplier = activity_multiplier(activity_level)
    result: dict[str, dict[str, float]] = {"macro": {}, "vitamin": {}, "mineral": {}}
    destinations = {
        "Basic_Components_DRI": "macro",
        "Vitamins_DRI": "vitamin",
        "Minerals_DRI": "mineral",
    }
    for table, fields in DRI_FIELDS.items():
        row = rows[table]
        if row is None:
            raise KeyError(f"No {table} row for Stage_ID={stage_id}, Life_ID={life_id}")
        result[destinations[table]] = {
            field: jdbc_float(row.get(field)) * multiplier for field in fields
        }
    return result


def adequacy_percent(
    eaten: Mapping[str, Mapping[str, float]],
    recommended: Mapping[str, Mapping[str, float]],
) -> dict[str, dict[str, float | None]]:
    """Legacy-style eaten/recommended*100 with undefined values represented as None."""
    result: dict[str, dict[str, float | None]] = {}
    for group, values in eaten.items():
        result[group] = {}
        for field, actual in values.items():
            expected = float(recommended[group].get(field, 0.0))
            result[group][field] = None if expected == 0 else float(actual) / expected * 100.0
    return result


def mean_vectors(vectors: Iterable[Iterable[float]]) -> list[float]:
    """Average recorded vectors by recorded-day/person count, as the Java UI did."""
    rows = [list(map(float, row)) for row in vectors]
    if not rows:
        return []
    width = len(rows[0])
    if any(len(row) != width for row in rows):
        raise ValueError("Cannot average legacy vectors with different lengths")
    return [sum(row[i] for row in rows) / len(rows) for i in range(width)]


__all__ = [
    "BASIC_FIELDS",
    "VITAMIN_FIELDS",
    "MINERAL_FIELDS",
    "POLY_FAT_FIELDS",
    "OTHER_FIELDS",
    "TOXICANT_FIELDS",
    "FoodPortion",
    "LegacyCalculation",
    "calculate_foods",
    "legacy_eaten_recommendation_vector",
    "legacy_recommendations",
    "adequacy_percent",
    "mean_vectors",
]
