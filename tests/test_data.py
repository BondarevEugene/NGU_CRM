import tempfile
import unittest
from pathlib import Path

from ngu_crm.data import add_event, connection, init_db


class DemoStorageTests(unittest.TestCase):
    def test_event_updates_status_and_retains_basis(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'demo.db'
            init_db(db)
            add_event(db, 1, 'leave', '2026-10-16', 'DEMO-ORDER-1', '')
            with connection(db) as session:
                self.assertEqual(session.execute('SELECT status FROM personnel WHERE id=1').fetchone()[0], 'leave')
                self.assertEqual(session.execute('SELECT basis_number FROM events').fetchone()[0], 'DEMO-ORDER-1')

    def test_rejects_missing_basis(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'demo.db'
            init_db(db)
            with self.assertRaises(ValueError):
                add_event(db, 1, 'leave', '2026-10-16', ' ', '')


if __name__ == '__main__':
    unittest.main()
