"""Writable modernization workspace built beside, never over, the legacy DB."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .constants import DAY_COLUMNS, TABLE_FIELDS
from .db import connect_writable, create_working_copy, sha256_file
from .legacy import FoodPortion, calculate_foods


MIGRATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS Python_Migration_Meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS Python_Recall_Day (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    Person_ID INTEGER NOT NULL,
    Day_Name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(Person_ID, Day_Name)
);
CREATE TABLE IF NOT EXISTS Python_Recall_Item (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    Recall_Day_ID INTEGER NOT NULL,
    Item_Order INTEGER NOT NULL,
    Food_ID INTEGER NOT NULL,
    Eaten_Weight_g REAL NOT NULL,
    FOREIGN KEY(Recall_Day_ID) REFERENCES Python_Recall_Day(id) ON DELETE CASCADE,
    UNIQUE(Recall_Day_ID, Item_Order)
);
"""


def initialize_workspace(source_db: str | Path, working_db: str | Path) -> dict[str, str]:
    checksums = create_working_copy(source_db, working_db)
    con = sqlite3.connect(Path(working_db).resolve())
    try:
        con.execute("PRAGMA foreign_keys = ON")
        con.executescript(MIGRATION_SCHEMA)
        con.execute(
            "INSERT OR REPLACE INTO Python_Migration_Meta(key, value) VALUES (?, ?)",
            ("source_sha256", checksums["source_sha256"]),
        )
        con.execute(
            "INSERT OR REPLACE INTO Python_Migration_Meta(key, value) VALUES (?, ?)",
            ("mode", "legacy_compatibility"),
        )
        con.commit()
    finally:
        con.close()
    return checksums


class WorkingRepository:
    """CRUD facade restricted to a database explicitly initialized as a copy."""

    PERSON_FIELDS = {
        "Surname", "Firstname", "Age", "Weight_KG", "Weight_LBS", "Height_CM",
        "Height_FT", "Height_IN", "BMI", "Activity_Level", "Gender", "Life_ID",
        "Usercode", "Unit",
    }
    FOOD_FIELDS = {"Food_ID", "Food_Name", "Food_Weight", "Food_Description"}

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise FileNotFoundError(self.path)
        con = self._connect()
        try:
            marker = con.execute(
                "SELECT value FROM Python_Migration_Meta WHERE key='mode'"
            ).fetchone()
            if marker is None or marker[0] != "legacy_compatibility":
                raise ValueError("Refusing writes: database is not a Python working copy")
        finally:
            con.close()

    def _connect(self) -> sqlite3.Connection:
        con = connect_writable(self.path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        return con

    @staticmethod
    def _validated(fields: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError("Unsupported fields: " + ", ".join(sorted(unknown)))
        return fields

    def add_person(self, fields: dict[str, Any]) -> int:
        data = self._validated(dict(fields), self.PERSON_FIELDS)
        if "Usercode" not in data:
            raise ValueError("Usercode is required because it is the legacy Person_ID business key")
        columns = list(data)
        placeholders = ",".join("?" for _ in columns)
        quoted = ",".join(f'"{name}"' for name in columns)
        with self._connect() as con:
            cur = con.execute(
                f"INSERT INTO Person ({quoted}) VALUES ({placeholders})",
                [data[name] for name in columns],
            )
            return int(cur.lastrowid)

    def update_person(self, person_id: int, fields: dict[str, Any]) -> None:
        """Update a participant by legacy Person.Usercode (the Java Person_ID)."""
        data = self._validated(dict(fields), self.PERSON_FIELDS)
        if not data:
            return
        assignments = ",".join(f'"{name}"=?' for name in data)
        with self._connect() as con:
            matches = int(con.execute("SELECT COUNT(*) FROM Person WHERE Usercode=?", (person_id,)).fetchone()[0])
            if matches != 1:
                raise ValueError(f"Usercode {person_id} resolves to {matches} Person rows; update is ambiguous")
            cur = con.execute(
                f"UPDATE Person SET {assignments} WHERE Usercode=?",
                [*data.values(), person_id],
            )
            if cur.rowcount < 1:
                raise KeyError(person_id)

    def delete_person(self, person_id: int) -> None:
        """Delete one legacy participant and related rows from the working copy only."""
        with self._connect() as con:
            matches = int(con.execute("SELECT COUNT(*) FROM Person WHERE Usercode=?", (person_id,)).fetchone()[0])
            if matches != 1:
                raise ValueError(f"Usercode {person_id} resolves to {matches} Person rows; delete is ambiguous")
            for table in ("Daily_Records", "Total_Average", "Weight_Gain_Loss", "Written_Notes"):
                con.execute(f'DELETE FROM "{table}" WHERE Person_ID=?', (person_id,))
            con.execute("DELETE FROM Python_Recall_Day WHERE Person_ID=?", (person_id,))
            con.execute("DELETE FROM Person WHERE Usercode=?", (person_id,))

    def add_food(self, fields: dict[str, Any]) -> int:
        data = self._validated(dict(fields), self.FOOD_FIELDS)
        if "Food_ID" not in data or "Food_Name" not in data:
            raise ValueError("Food_ID and Food_Name are required")
        columns = list(data)
        with self._connect() as con:
            cur = con.execute(
                f"INSERT INTO Food ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",
                [data[name] for name in columns],
            )
            return int(cur.lastrowid)

    def update_food(self, food_id: int, fields: dict[str, Any]) -> None:
        data = self._validated(dict(fields), self.FOOD_FIELDS - {"Food_ID"})
        if not data:
            return
        assignments = ",".join(f'"{name}"=?' for name in data)
        with self._connect() as con:
            old = con.execute("SELECT Food_Name FROM Food WHERE Food_ID=? LIMIT 1", (food_id,)).fetchone()
            if old is None:
                raise KeyError(food_id)
            cur = con.execute(
                f"UPDATE Food SET {assignments} WHERE Food_ID=?",
                [*data.values(), food_id],
            )
            if cur.rowcount < 1:
                raise KeyError(food_id)
            if "Food_Name" in data:
                for table in TABLE_FIELDS:
                    con.execute(
                        f'UPDATE "{table}" SET Food_Name=? WHERE Food_ID=? AND Food_Name=?',
                        (data["Food_Name"], food_id, old[0]),
                    )

    def upsert_food_composition(self, food_id: int, table: str, values: dict[str, Any]) -> None:
        """Add/update one composition section for a food in the working copy."""
        if table not in TABLE_FIELDS:
            raise ValueError(f"Unsupported composition table {table!r}")
        data = self._validated(dict(values), set(TABLE_FIELDS[table]))
        if not data:
            return
        with self._connect() as con:
            food = con.execute("SELECT Food_Name FROM Food WHERE Food_ID=? LIMIT 1", (food_id,)).fetchone()
            if food is None:
                raise KeyError(food_id)
            food_name = str(food[0])
            existing = con.execute(
                f'SELECT id FROM "{table}" WHERE Food_ID=? AND Food_Name=? ORDER BY id LIMIT 1',
                (food_id, food_name),
            ).fetchone()
            if existing:
                assignments = ",".join(f'"{name}"=?' for name in data)
                con.execute(
                    f'UPDATE "{table}" SET {assignments} WHERE id=?',
                    [*data.values(), existing[0]],
                )
            else:
                marker = table
                columns = ["Food_ID", "Food_Name", marker, *data.keys()]
                con.execute(
                    f'INSERT INTO "{table}" ({",".join(f"{name}" for name in columns)}) '
                    f'VALUES ({",".join("?" for _ in columns)})',
                    [food_id, food_name, 0.0, *data.values()],
                )

    def delete_food(self, food_id: int) -> None:
        """Delete a food from the working copy if no Python recall currently uses it."""
        with self._connect() as con:
            in_use = int(
                con.execute("SELECT COUNT(*) FROM Python_Recall_Item WHERE Food_ID=?", (food_id,)).fetchone()[0]
            )
            if in_use:
                raise ValueError(f"Food_ID {food_id} is used by {in_use} Python recall items")
            for table in TABLE_FIELDS:
                con.execute(f'DELETE FROM "{table}" WHERE Food_ID=?', (food_id,))
            cur = con.execute("DELETE FROM Food WHERE Food_ID=?", (food_id,))
            if cur.rowcount < 1:
                raise KeyError(food_id)

    def upsert_note(self, person_id: int, person_name: str, note: str) -> None:
        with self._connect() as con:
            existing = con.execute(
                "SELECT id FROM Written_Notes WHERE Person_ID=? ORDER BY id LIMIT 1", (person_id,)
            ).fetchone()
            if existing:
                con.execute("UPDATE Written_Notes SET Person_Name=?, Note=? WHERE id=?", (person_name, note, existing[0]))
            else:
                con.execute(
                    "INSERT INTO Written_Notes(Person_ID, Person_Name, Note) VALUES (?,?,?)",
                    (person_id, person_name, note),
                )

    def save_recall(self, person_id: int, day: str, portions: Iterable[FoodPortion]) -> int:
        if day not in DAY_COLUMNS:
            raise ValueError(f"Day must be one of: {', '.join(DAY_COLUMNS)}")
        items = tuple(portions)
        if any(item.grams < 0 for item in items):
            raise ValueError("Food weight cannot be negative")
        with self._connect() as con:
            if con.execute("SELECT 1 FROM Person WHERE Usercode=?", (person_id,)).fetchone() is None:
                raise KeyError(f"Person id {person_id}")
            for item in items:
                if con.execute("SELECT 1 FROM Food WHERE Food_ID=?", (item.food_id,)).fetchone() is None:
                    raise KeyError(f"Food_ID {item.food_id}")
            con.execute(
                "INSERT INTO Python_Recall_Day(Person_ID, Day_Name) VALUES (?,?) "
                "ON CONFLICT(Person_ID, Day_Name) DO UPDATE SET updated_at=CURRENT_TIMESTAMP",
                (person_id, day),
            )
            recall_id = int(
                con.execute(
                    "SELECT id FROM Python_Recall_Day WHERE Person_ID=? AND Day_Name=?",
                    (person_id, day),
                ).fetchone()[0]
            )
            con.execute("DELETE FROM Python_Recall_Item WHERE Recall_Day_ID=?", (recall_id,))
            con.executemany(
                "INSERT INTO Python_Recall_Item(Recall_Day_ID, Item_Order, Food_ID, Eaten_Weight_g) VALUES (?,?,?,?)",
                [(recall_id, i, item.food_id, item.grams) for i, item in enumerate(items)],
            )
            return recall_id

    def recall(self, person_id: int, day: str) -> list[FoodPortion]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT i.Food_ID, i.Eaten_Weight_g FROM Python_Recall_Day d "
                "JOIN Python_Recall_Item i ON i.Recall_Day_ID=d.id "
                "WHERE d.Person_ID=? AND d.Day_Name=? ORDER BY i.Item_Order",
                (person_id, day),
            )
            return [FoodPortion(int(row[0]), float(row[1])) for row in rows]
