import tempfile
import unittest
from pathlib import Path

from tools.learning import Brain


class LearningStorageTest(unittest.TestCase):
    def test_brain_sqlite_uses_normal_synchronous(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "brain.db"
            brain = Brain(str(db_path))
            conn = brain._get_conn()
            try:
                synchronous = conn.execute("PRAGMA synchronous").fetchone()[0]
            finally:
                conn.close()

        self.assertEqual(int(synchronous), 1)


if __name__ == "__main__":
    unittest.main()
