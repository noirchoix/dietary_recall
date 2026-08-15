"""Fail a deployment build if research data or local secrets are tracked."""

from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DATABASE_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
SENSITIVE_PARTS = {"secrets", "raw_tables"}


def tracked_paths() -> list[Path]:
    top = subprocess.run(
        ["git", "-C", str(ROOT), "rev-parse", "--show-toplevel"],
        check=False,
        capture_output=True,
        text=True,
    )
    if top.returncode != 0 or Path(top.stdout.strip()).resolve() != ROOT:
        return [path.relative_to(ROOT) for path in ROOT.rglob("*") if path.is_file()]
    result = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"],
        check=False,
        capture_output=True,
    )
    if result.returncode == 0:
        return [Path(value.decode("utf-8")) for value in result.stdout.split(b"\0") if value]
    return [path.relative_to(ROOT) for path in ROOT.rglob("*") if path.is_file()]


def violations(paths: list[Path]) -> list[str]:
    invalid: list[str] = []
    for path in paths:
        lowered = {part.casefold() for part in path.parts}
        name = path.name.casefold()
        if path.suffix.casefold() in DATABASE_SUFFIXES or any(name.endswith(f"{suffix}-wal") or name.endswith(f"{suffix}-shm") for suffix in DATABASE_SUFFIXES):
            invalid.append(str(path))
        elif lowered & SENSITIVE_PARTS:
            invalid.append(str(path))
        elif name == ".env" or (name.startswith(".env.") and name != ".env.example"):
            invalid.append(str(path))
    return sorted(set(invalid))


def main() -> int:
    invalid = violations(tracked_paths())
    if invalid:
        print("Deployment blocked: sensitive or generated files are tracked:")
        for path in invalid:
            print(f"  - {path}")
        return 1
    print("Repository safety check passed: no tracked databases, raw tables, secrets or local environment files.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
