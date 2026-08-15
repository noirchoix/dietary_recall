"""Deterministic descriptive/data-quality audit for a legacy snapshot."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .constants import COMPOSITION_TABLES, DAY_COLUMNS, TABLE_FIELDS
from .db import LegacyRepository, readonly, sha256_file
from .serialization import blob_inventory


def snapshot_audit(db_path: str | Path) -> dict[str, Any]:
    repo = LegacyRepository(db_path)
    tables = repo.tables()
    report: dict[str, Any] = {
        "source": str(Path(db_path).resolve()),
        "sha256": sha256_file(db_path),
        "integrity_check": repo.integrity_check(),
        "table_count": len(tables),
        "tables": {},
    }
    with readonly(db_path) as con:
        for table in tables:
            columns = [dict(row) for row in con.execute(f'PRAGMA table_info("{table}")')]
            row_count = int(con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
            report["tables"][table] = {
                "rows": row_count,
                "columns": [row["name"] for row in columns],
            }

        food_ids = {row[0] for row in con.execute("SELECT Food_ID FROM Food")}
        orphan_counts: dict[str, int] = {}
        completeness: dict[str, dict[str, int]] = {}
        for table in COMPOSITION_TABLES:
            rows = list(con.execute(f'SELECT * FROM "{table}"'))
            orphan_counts[table] = sum(1 for row in rows if row["Food_ID"] not in food_ids)
            completeness[table] = {
                field: sum(1 for row in rows if row[field] is not None)
                for field in TABLE_FIELDS[table]
            }

        duplicate_codes = [
            {"usercode": row[0], "count": row[1]}
            for row in con.execute(
                "SELECT Usercode, COUNT(*) FROM Person GROUP BY Usercode HAVING COUNT(*) > 1 ORDER BY COUNT(*) DESC"
            )
        ]
        weekday_blobs = {
            day: int(con.execute(f'SELECT COUNT(*) FROM Daily_Records WHERE "{day}" IS NOT NULL').fetchone()[0])
            for day in DAY_COLUMNS
        }

    inventory = blob_inventory(db_path)
    report["quality"] = {
        "composition_orphans_by_table": orphan_counts,
        "composition_non_null_counts": completeness,
        "duplicate_person_usercodes": duplicate_codes,
        "weekday_blob_counts": weekday_blobs,
        "blob_evidence_count": len(inventory),
        "java_stream_blob_count": sum(item.java_serialized for item in inventory),
        "blob_tables": dict(Counter(item.table for item in inventory)),
    }
    return report

