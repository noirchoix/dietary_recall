from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dietary_recall.db import connect_writable


class ConnectionLifecycleTests(unittest.TestCase):
    def test_context_manager_commits_and_closes_file_handle(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "lifecycle.db"
            with connect_writable(database) as con:
                con.execute("CREATE TABLE evidence(value TEXT NOT NULL)")
                con.execute("INSERT INTO evidence(value) VALUES ('committed')")

            with self.assertRaises(sqlite3.ProgrammingError):
                con.execute("SELECT 1")

            check = sqlite3.connect(database)
            try:
                self.assertEqual(
                    "committed",
                    check.execute("SELECT value FROM evidence").fetchone()[0],
                )
            finally:
                check.close()

    def test_context_manager_rolls_back_and_closes_file_handle(self):
        with tempfile.TemporaryDirectory() as tmp:
            database = Path(tmp) / "rollback.db"
            initial = sqlite3.connect(database)
            try:
                initial.execute("CREATE TABLE evidence(value TEXT NOT NULL)")
                initial.commit()
            finally:
                initial.close()

            with self.assertRaisesRegex(RuntimeError, "force rollback"):
                with connect_writable(database) as con:
                    con.execute("INSERT INTO evidence(value) VALUES ('discarded')")
                    raise RuntimeError("force rollback")

            with self.assertRaises(sqlite3.ProgrammingError):
                con.execute("SELECT 1")

            check = sqlite3.connect(database)
            try:
                self.assertEqual(0, check.execute("SELECT COUNT(*) FROM evidence").fetchone()[0])
            finally:
                check.close()


if __name__ == "__main__":
    unittest.main()
