import tempfile
import unittest
from pathlib import Path

from ngu_crm.data import (add_absence, add_duty, add_event, connection, init_db,
                          issue_asset, return_asset, add_document, advance_document,
                          appoint, add_unit, add_slot)


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

    def test_asset_cannot_be_issued_twice_and_returned_before_issue(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'demo.db'
            init_db(db)
            issue_asset(db, 1, 1, '2026-10-16', 'DEMO-DOC-1')
            with self.assertRaises(Exception):
                issue_asset(db, 1, 2, '2026-10-16', 'DEMO-DOC-2')
            with self.assertRaises(ValueError):
                return_asset(db, 1, '2026-10-15')
            return_asset(db, 1, '2026-10-17')
            issue_asset(db, 1, 2, '2026-10-18', 'DEMO-DOC-3')

    def test_approved_absence_blocks_duty(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'demo.db'
            init_db(db)
            add_absence(db, 1, 'annual_leave', '2026-10-16', '2026-10-20', 'DEMO-ORDER')
            with connection(db) as session:
                session.execute("UPDATE absence SET state='approved' WHERE id=1")
            with self.assertRaises(ValueError):
                add_duty(db, 1, '2026-10-18', 'Демо чергування', 'DEMO-ORDER-2')

    def test_appointment_requires_registered_order_and_unique_slot(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'demo.db'
            init_db(db)
            add_document(db, 'order', 'Демо наказ', 'DEMO-ORD-1', '2026-10-16')
            with self.assertRaises(ValueError):
                appoint(db, 1, 1, 1, '2026-10-17')
            for _ in range(3):
                advance_document(db, 1)
            appoint(db, 1, 1, 1, '2026-10-17')
            with self.assertRaises(Exception):
                appoint(db, 2, 1, 1, '2026-10-17')

    def test_units_and_slots_are_distinct(self):
        with tempfile.TemporaryDirectory() as folder:
            db = Path(folder) / 'demo.db'
            init_db(db)
            add_unit(db, 'DEMO-U3', 'Демо секція', 2)
            add_slot(db, 3, 'DEMO-P3', 'Демо посада 3', 'Демо')
            with connection(db) as session:
                self.assertEqual(session.execute('SELECT count(*) FROM staff_slot').fetchone()[0], 2)


if __name__ == '__main__':
    unittest.main()
