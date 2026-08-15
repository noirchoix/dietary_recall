"""SQLite access with an immutable-source / writable-copy boundary."""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


class ClosingConnection(sqlite3.Connection):
    """SQLite connection whose context manager also releases the file handle.

    ``sqlite3.Connection.__exit__`` only commits or rolls back; it does not
    close the connection.  That leaves database files locked on Windows when
    callers use the otherwise-natural ``with repository.connect()`` pattern.
    """

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        try:
            return bool(super().__exit__(exc_type, exc_value, traceback))
        finally:
            self.close()


def connect_writable(database: str | Path, *, timeout: float = 5.0) -> sqlite3.Connection:
    """Open a writable connection that closes after a context-manager block."""
    return sqlite3.connect(database, timeout=timeout, factory=ClosingConnection)


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def connect_readonly(path: str | Path) -> sqlite3.Connection:
    resolved = Path(path).resolve()
    if not resolved.is_file():
        raise FileNotFoundError(resolved)
    connection = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only = ON")
    return connection


@contextmanager
def readonly(path: str | Path) -> Iterator[sqlite3.Connection]:
    connection = connect_readonly(path)
    try:
        yield connection
    finally:
        connection.close()


def create_working_copy(source: str | Path, destination: str | Path) -> dict[str, str]:
    """Copy a source DB after hashing it, then verify the copy is identical."""
    source_path = Path(source).resolve()
    destination_path = Path(destination).resolve()
    if source_path == destination_path:
        raise ValueError("Working database must not overwrite the source database")
    if destination_path.exists():
        raise FileExistsError(destination_path)
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    before = sha256_file(source_path)
    shutil.copy2(source_path, destination_path)
    after_source = sha256_file(source_path)
    copied = sha256_file(destination_path)
    if before != after_source or before != copied:
        raise RuntimeError("Source/copy checksum mismatch; refusing to continue")
    return {"source_sha256": before, "working_copy_sha256": copied}


class LegacyRepository:
    """Read-only query facade over a legacy Nutrients.db snapshot."""

    def __init__(self, path: str | Path):
        self.path = Path(path).resolve()

    def tables(self) -> list[str]:
        with readonly(self.path) as con:
            return [
                row[0]
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]

    def table_columns(self, table: str) -> list[sqlite3.Row]:
        if table not in self.tables():
            raise KeyError(table)
        with readonly(self.path) as con:
            return list(con.execute(f"PRAGMA table_info({quote_identifier(table)})"))

    def table_count(self, table: str) -> int:
        if table not in self.tables():
            raise KeyError(table)
        with readonly(self.path) as con:
            return int(con.execute(f"SELECT COUNT(*) FROM {quote_identifier(table)}").fetchone()[0])

    def integrity_check(self) -> str:
        with readonly(self.path) as con:
            return str(con.execute("PRAGMA integrity_check").fetchone()[0])

    def search_foods(self, query: str = "", limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT * FROM Food"
        params: list[Any] = []
        if query:
            sql += " WHERE Food_Name LIKE ? OR CAST(Food_ID AS TEXT) = ?"
            params.extend([f"%{query}%", query])
        sql += " ORDER BY Food_Name LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        with readonly(self.path) as con:
            return [dict(row) for row in con.execute(sql, params)]

    def search_people(self, query: str = "", limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT * FROM Person"
        params: list[Any] = []
        if query:
            sql += (
                " WHERE Surname LIKE ? OR Firstname LIKE ? OR CAST(Usercode AS TEXT) = ?"
            )
            params.extend([f"%{query}%", f"%{query}%", query])
        sql += " ORDER BY id LIMIT ?"
        params.append(max(1, min(int(limit), 1000)))
        with readonly(self.path) as con:
            return [dict(row) for row in con.execute(sql, params)]

    def food_row(self, food_id: int) -> dict[str, Any]:
        with readonly(self.path) as con:
            row = con.execute("SELECT * FROM Food WHERE Food_ID = ?", (food_id,)).fetchone()
            if row is None:
                raise KeyError(f"Food_ID {food_id} not found")
            return dict(row)

    def composition_row(self, table: str, food_id: int, food_name: str | None = None) -> dict[str, Any] | None:
        if table not in {"Basic_Components", "Vitamins", "Minerals", "Poly_Fats", "Other_Nutrients", "Toxicants"}:
            raise KeyError(table)
        sql = f"SELECT * FROM {quote_identifier(table)} WHERE Food_ID = ?"
        params: list[Any] = [food_id]
        if food_name is not None:
            sql += " AND Food_Name = ?"
            params.append(food_name)
        sql += " LIMIT 1"
        with readonly(self.path) as con:
            row = con.execute(sql, params).fetchone()
            return dict(row) if row is not None else None

    def dri_rows(self, stage_id: int, life_id: int) -> dict[str, dict[str, Any] | None]:
        result: dict[str, dict[str, Any] | None] = {}
        with readonly(self.path) as con:
            for table in ("Basic_Components_DRI", "Vitamins_DRI", "Minerals_DRI"):
                row = con.execute(
                    f"SELECT * FROM {quote_identifier(table)} WHERE Stage_ID = ? AND Life_ID = ? LIMIT 1",
                    (stage_id, life_id),
                ).fetchone()
                result[table] = dict(row) if row is not None else None
        return result
