"""Local demonstration storage. Never import operational or personal records here."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS personnel (
 id INTEGER PRIMARY KEY, display_name TEXT NOT NULL, service_number TEXT UNIQUE NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('active','leave','transferred','excluded')),
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS events (
 id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES personnel(id),
 kind TEXT NOT NULL CHECK(kind IN ('enrolled','assigned','leave','returned','transferred','excluded','correction')),
 effective_date TEXT NOT NULL, basis_number TEXT NOT NULL, note TEXT NOT NULL DEFAULT '',
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_events_person_date ON events(person_id,effective_date);
"""


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        if not db.execute('SELECT 1 FROM personnel LIMIT 1').fetchone():
            db.executemany(
                'INSERT INTO personnel(display_name,service_number,status) VALUES (?,?,?)',
                [('Демо Особа А', 'DEMO-001', 'active'), ('Демо Особа Б', 'DEMO-002', 'leave')],
            )


@contextmanager
def connection(path: Path):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def add_event(path: Path, person_id: int, kind: str, date: str, basis: str, note: str) -> None:
    if kind not in {'enrolled', 'assigned', 'leave', 'returned', 'transferred', 'excluded', 'correction'}:
        raise ValueError('Unsupported event')
    if not basis.strip() or len(basis) > 120 or len(note) > 500:
        raise ValueError('Basis required; input too long')
    from datetime import date as date_type
    date_type.fromisoformat(date)
    with connection(path) as db:
        if not db.execute('SELECT 1 FROM personnel WHERE id=?', (person_id,)).fetchone():
            raise ValueError('Unknown person')
        db.execute(
            'INSERT INTO events(person_id,kind,effective_date,basis_number,note) VALUES (?,?,?,?,?)',
            (person_id, kind, date, basis.strip(), note.strip()),
        )
        status = {'enrolled': 'active', 'returned': 'active', 'leave': 'leave',
                  'transferred': 'transferred', 'excluded': 'excluded'}.get(kind)
        if status:
            db.execute('UPDATE personnel SET status=? WHERE id=?', (status, person_id))
