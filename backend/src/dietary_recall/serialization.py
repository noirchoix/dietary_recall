"""Evidence inventory and optional Java bridge for serialized legacy BLOBs."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .constants import DAY_COLUMNS
from .db import readonly


JAVA_STREAM_MAGIC = b"\xAC\xED\x00\x05"


@dataclass(frozen=True)
class BlobEvidence:
    table: str
    row_id: int
    person_id: int | None
    column: str
    byte_length: int
    sha256: str
    java_serialized: bool


def blob_inventory(db_path: str | Path) -> list[BlobEvidence]:
    evidence: list[BlobEvidence] = []
    with readonly(db_path) as con:
        for row in con.execute("SELECT * FROM Daily_Records ORDER BY id"):
            for day in DAY_COLUMNS:
                blob = row[day]
                if blob is None:
                    continue
                data = bytes(blob)
                evidence.append(
                    BlobEvidence(
                        table="Daily_Records",
                        row_id=int(row["id"]),
                        person_id=row["Person_ID"],
                        column=day,
                        byte_length=len(data),
                        sha256=hashlib.sha256(data).hexdigest(),
                        java_serialized=data.startswith(JAVA_STREAM_MAGIC),
                    )
                )
        for row in con.execute("SELECT * FROM Total_Average ORDER BY id"):
            blob = row["Total_Average_Daily"]
            if blob is None:
                continue
            data = bytes(blob)
            evidence.append(
                BlobEvidence(
                    table="Total_Average",
                    row_id=int(row["id"]),
                    person_id=row["Person_ID"],
                    column="Total_Average_Daily",
                    byte_length=len(data),
                    sha256=hashlib.sha256(data).hexdigest(),
                    java_serialized=data.startswith(JAVA_STREAM_MAGIC),
                )
            )
    return evidence


def decode_with_java(
    db_path: str | Path,
    helper_java: str | Path,
    application_jar: str | Path,
    sqlite_jdbc_jar: str | Path,
) -> list[dict[str, Any]]:
    """Decode Java BLOBs without changing the original database.

    Java 11+ source-file mode compiles the small bridge in memory.  Historical
    application classes are used only as serialization type definitions.
    """
    java = shutil.which("java")
    if not java:
        raise RuntimeError("Java runtime not found; BLOB inventory is still available")
    paths = [Path(helper_java), Path(application_jar), Path(sqlite_jdbc_jar), Path(db_path)]
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError("Missing decoder input(s): " + ", ".join(missing))
    classpath = f"{Path(application_jar).resolve()}:{Path(sqlite_jdbc_jar).resolve()}"
    command = [
        java,
        "--class-path",
        classpath,
        str(Path(helper_java).resolve()),
        str(Path(db_path).resolve()),
    ]
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"Java decoder failed ({completed.returncode}): {completed.stderr.strip()}")
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(completed.stdout.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Invalid decoder JSON on line {line_number}") from exc
    return records

