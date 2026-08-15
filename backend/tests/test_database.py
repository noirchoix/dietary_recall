from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from dietary_recall.db import connect_readonly, create_working_copy, sha256_file


class DatabaseBoundaryTests(unittest.TestCase):
    def make_db(self, root: Path) -> Path:
        path = root / "source.db"
        con = sqlite3.connect(path)
        try:
            con.execute("CREATE TABLE example(id INTEGER PRIMARY KEY, value TEXT)")
            con.execute("INSERT INTO example(value) VALUES ('evidence')")
            con.commit()
        finally:
            con.close()
        return path

    def test_readonly_connection_rejects_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self.make_db(Path(tmp))
            con = connect_readonly(source)
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    con.execute("INSERT INTO example(value) VALUES ('no')")
            finally:
                con.close()

    def test_working_copy_keeps_source_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = self.make_db(root)
            before = sha256_file(source)
            target = root / "working.db"
            checksums = create_working_copy(source, target)
            self.assertEqual(before, sha256_file(source))
            self.assertEqual(checksums["source_sha256"], checksums["working_copy_sha256"])

    def test_copy_cannot_overwrite_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = self.make_db(Path(tmp))
            with self.assertRaises(ValueError):
                create_working_copy(source, source)


if __name__ == "__main__":
    unittest.main()

