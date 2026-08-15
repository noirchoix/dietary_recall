"""Golden-master comparison of decoded Java daily totals vs Python recomputation."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any

from .constants import TABLE_FIELDS
from .db import LegacyRepository
from .legacy import FoodPortion, calculate_foods


VECTOR_TABLES = {
    "total_eaten_basic_components": "Basic_Components",
    "total_eaten_vitamins": "Vitamins",
    "total_eaten_minerals": "Minerals",
    "total_eaten_poly_fats": "Poly_Fats",
    "total_eaten_other_nutrients": "Other_Nutrients",
    "total_eaten_toxicants": "Toxicants",
}


@dataclass(frozen=True)
class DailyParityResult:
    row_id: int
    person_id: int
    day: str
    status: str
    item_count: int
    compared_values: int
    mismatched_values: int
    max_abs_difference: float
    detail: str = ""


def validate_decoded_daily(
    repository: LegacyRepository,
    decoded_records: list[dict[str, Any]],
    tolerance: float = 1e-3,
) -> list[DailyParityResult]:
    results: list[DailyParityResult] = []
    for record in decoded_records:
        if record.get("kind") != "daily_record":
            continue
        if record.get("status") != "decoded":
            results.append(
                DailyParityResult(
                    int(record.get("row_id", -1)),
                    int(record.get("db_person_id", -1)),
                    str(record.get("column", "")),
                    "decode_error",
                    0,
                    0,
                    0,
                    0.0,
                    str(record.get("error", "")),
                )
            )
            continue
        obj = record.get("object", {})
        ids = obj.get("food_id") or []
        weights = obj.get("eaten_food_weight") or []
        if len(ids) != len(weights):
            results.append(
                DailyParityResult(
                    int(record["row_id"]),
                    int(record.get("db_person_id", obj.get("person_id", -1))),
                    str(record.get("column", "")),
                    "invalid_item_vectors",
                    max(len(ids), len(weights)),
                    0,
                    0,
                    0.0,
                    f"food_id length {len(ids)} != eaten_food_weight length {len(weights)}",
                )
            )
            continue
        portions = [FoodPortion(int(food_id), float(grams)) for food_id, grams in zip(ids, weights)]
        try:
            calculated = calculate_foods(repository, portions)
        except (KeyError, ValueError) as exc:
            results.append(
                DailyParityResult(
                    int(record["row_id"]),
                    int(record.get("db_person_id", obj.get("person_id", -1))),
                    str(record.get("column", "")),
                    "recompute_error",
                    len(portions),
                    0,
                    0,
                    0.0,
                    str(exc),
                )
            )
            continue

        compared = 0
        mismatched = 0
        max_difference = 0.0
        for vector_name, table in VECTOR_TABLES.items():
            stored = obj.get(vector_name) or []
            fields = TABLE_FIELDS[table]
            # Java vectors store summed food weight at index 0, then nutrients.
            for index, field in enumerate(fields, start=1):
                if index >= len(stored):
                    continue
                actual = float(calculated.totals[table][field])
                historical = float(stored[index])
                difference = abs(actual - historical)
                compared += 1
                max_difference = max(max_difference, difference)
                if difference > tolerance:
                    mismatched += 1
        results.append(
            DailyParityResult(
                int(record["row_id"]),
                int(record.get("db_person_id", obj.get("person_id", -1))),
                str(record.get("column", "")),
                "match" if mismatched == 0 else "mismatch",
                len(portions),
                compared,
                mismatched,
                max_difference,
            )
        )
    return results


def parity_summary(results: list[DailyParityResult]) -> dict[str, Any]:
    statuses: dict[str, int] = {}
    for result in results:
        statuses[result.status] = statuses.get(result.status, 0) + 1
    compared = [result for result in results if result.compared_values]
    return {
        "daily_records": len(results),
        "status_counts": statuses,
        "compared_values": sum(result.compared_values for result in results),
        "mismatched_values": sum(result.mismatched_values for result in results),
        "max_abs_difference": max((result.max_abs_difference for result in compared), default=0.0),
    }

