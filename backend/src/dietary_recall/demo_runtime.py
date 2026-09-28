"""Deterministic, synthetic workspace bootstrap for disposable demonstrations.

This module never copies or opens the PhD research snapshot.  It creates a
small illustrative legacy database, migrates that database through the normal
v0.4 schema path, and creates an independent credential store.  The resulting
files are intended for ephemeral hosting only and may be discarded at any
time.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

from .constants import TABLE_FIELDS
from .security import CredentialStore
from .v03_schema import DEFAULT_OWNER_UID, DEFAULT_PROJECT_UID
from .v04_schema import initialize_v04_research_database


DEMO_EMAIL = "demo@dietary-recall.local"


@dataclass(frozen=True)
class DemoWorkspace:
    root: str
    legacy_database: str
    research_database: str
    credential_store: str
    email: str
    reset_on_start: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _safe_root(value: str | Path) -> Path:
    root = Path(value).expanduser().resolve()
    if root == Path(root.anchor) or len(root.parts) < 3:
        raise ValueError("Demo directory must be a specific, non-root path")
    return root


def _create_synthetic_legacy(path: Path) -> None:
    foods = (
        (1, 1001, "Illustrative jollof rice sample", "Synthetic demonstration record; not a reference composition value"),
        (2, 1002, "Illustrative bean porridge sample", "Synthetic demonstration record; not a reference composition value"),
        (3, 1003, "Illustrative yam pottage sample", "Synthetic demonstration record; not a reference composition value"),
        (4, 1004, "Illustrative vegetable soup sample", "Synthetic demonstration record; not a reference composition value"),
    )
    people = tuple(
        (
            index,
            9000 + index,
            "Demo",
            f"Participant {index:02d}",
            20 + index,
            "1" if index % 2 else "2",
            str(1 + (index % 4)),
            4,
            55 + index,
            158 + index,
            round((55 + index) / (((158 + index) / 100) ** 2), 2),
        )
        for index in range(1, 9)
    )

    # Deliberately sparse, clearly synthetic composition values.  Earlier demo
    # builds filled almost every field with a numeric sequence.  That exercised
    # migration code, but it also made the public calculator look as though it
    # contained comprehensive measured composition.  These values exist only
    # to demonstrate scaling and missingness; they are not reference data.
    composition: dict[int, dict[str, dict[str, float]]] = {
        1001: {
            "Basic_Components": {
                "Calories_kcal": 150.0, "Protein_g": 3.0, "Carbohydrates_g": 28.0,
                "Dietary_Fibre_g": 2.0, "Fat_g": 3.0, "Water_g": 65.0,
            },
            "Minerals": {"Iron_mg": 1.2, "Potassium_mg": 120.0, "Sodium_mg": 180.0},
        },
        1002: {
            "Basic_Components": {
                "Calories_kcal": 132.0, "Protein_g": 7.2, "Carbohydrates_g": 20.5,
                "Dietary_Fibre_g": 5.3, "Fat_g": 2.5, "Water_g": 63.0,
            },
            "Minerals": {"Calcium_mg": 42.0, "Iron_mg": 2.1, "Potassium_mg": 260.0},
        },
        1003: {
            "Basic_Components": {
                "Calories_kcal": 118.0, "Protein_g": 2.1, "Carbohydrates_g": 24.0,
                "Dietary_Fibre_g": 2.7, "Fat_g": 1.8, "Water_g": 69.0,
            },
            "Minerals": {"Iron_mg": 0.9, "Potassium_mg": 240.0, "Sodium_mg": 95.0},
        },
        1004: {
            "Basic_Components": {
                "Calories_kcal": 84.0, "Protein_g": 4.0, "Carbohydrates_g": 7.5,
                "Dietary_Fibre_g": 3.2, "Fat_g": 4.2, "Water_g": 78.0,
            },
            "Minerals": {"Calcium_mg": 68.0, "Iron_mg": 2.4, "Potassium_mg": 310.0},
            "Toxicants": {"Cadmium_mcg": 0.8},
        },
    }

    con = sqlite3.connect(path)
    try:
        con.execute(
            "CREATE TABLE Food(id INTEGER PRIMARY KEY, Food_ID INTEGER, Food_Name TEXT, Food_Description TEXT)"
        )
        con.executemany("INSERT INTO Food VALUES (?,?,?,?)", foods)
        con.execute(
            "CREATE TABLE Person(id INTEGER PRIMARY KEY, Usercode INTEGER, Surname TEXT, Firstname TEXT, "
            "Age REAL, Gender TEXT, Activity_Level TEXT, Life_ID INTEGER, Weight_KG REAL, Height_CM REAL, BMI REAL)"
        )
        con.executemany("INSERT INTO Person VALUES (?,?,?,?,?,?,?,?,?,?,?)", people)

        for table, fields in TABLE_FIELDS.items():
            definitions = ",".join(f'"{field}" REAL' for field in fields)
            con.execute(
                f'CREATE TABLE "{table}"(id INTEGER PRIMARY KEY, Food_ID INTEGER, Food_Name TEXT, {definitions})'
            )
            placeholders = ",".join("?" for _ in range(3 + len(fields)))
            for food_index, (_, food_id, food_name, _) in enumerate(foods, start=1):
                values = [composition.get(food_id, {}).get(table, {}).get(field) for field in fields]
                con.execute(
                    f'INSERT INTO "{table}" VALUES ({placeholders})',
                    [food_index, food_id, food_name, *values],
                )
        con.commit()
    finally:
        con.close()


def initialize_demo_workspace(
    root: str | Path,
    password: str,
    *,
    email: str = DEMO_EMAIL,
    reset: bool = True,
) -> DemoWorkspace:
    """Create or reuse a disposable synthetic demonstration workspace."""
    target = _safe_root(root)
    target.mkdir(parents=True, exist_ok=True)
    legacy = target / "Nutrients-demo-synthetic.db"
    research = target / "Nutrients-research-demo.db"
    credentials = target / "dietary-recall-demo-auth.db"

    if reset:
        for path in (legacy, research, credentials):
            path.unlink(missing_ok=True)

    existing = [path.exists() for path in (legacy, research, credentials)]
    if any(existing) and not all(existing):
        raise ValueError("Demo workspace is incomplete; restart with reset enabled")

    normalized_email = email.strip().casefold()
    if not all(existing):
        _create_synthetic_legacy(legacy)
        initialize_v04_research_database(legacy, research)
        con = sqlite3.connect(research)
        try:
            con.execute("PRAGMA foreign_keys = ON")
            con.execute(
                "UPDATE app_users SET email=?,display_name='Demo Researcher' WHERE user_uid=?",
                (normalized_email, DEFAULT_OWNER_UID),
            )
            con.execute(
                "UPDATE research_projects SET project_code='NPF-DEMO',project_name='Nigerian Prepared Foods — Demonstration',"
                "description='Disposable synthetic workspace for demonstrating governed dietary-recall workflows',"
                "created_by=? WHERE project_uid=?",
                (normalized_email, DEFAULT_PROJECT_UID),
            )
            con.execute(
                "UPDATE project_records SET linked_by=? WHERE project_uid=?",
                (normalized_email, DEFAULT_PROJECT_UID),
            )
            con.executemany(
                "INSERT OR REPLACE INTO research_meta(key,value) VALUES (?,?)",
                (
                    ("platform_release", "0.5.1"),
                    ("deployment_mode", "ephemeral_synthetic_demo"),
                    ("demo_data_policy", "synthetic_only_resets_on_service_restart"),
                ),
            )
            con.commit()
        finally:
            con.close()

        store = CredentialStore.initialize(credentials)
        store.set_password(normalized_email, DEFAULT_OWNER_UID, password)

    return DemoWorkspace(
        root=str(target),
        legacy_database=str(legacy),
        research_database=str(research),
        credential_store=str(credentials),
        email=normalized_email,
        reset_on_start=reset,
    )
