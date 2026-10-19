"""Local demonstration storage. Never import operational or personal records here."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS app_role (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE);
CREATE TABLE IF NOT EXISTS app_user (id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE,
 password_hash TEXT NOT NULL, role_id INTEGER REFERENCES app_role(id), is_owner INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS role_permission (role_id INTEGER NOT NULL REFERENCES app_role(id),
 section TEXT NOT NULL, action TEXT NOT NULL, PRIMARY KEY(role_id,section,action));
CREATE TABLE IF NOT EXISTS audit_event (id INTEGER PRIMARY KEY, actor_id INTEGER NOT NULL,
 action TEXT NOT NULL, route TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
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
CREATE TABLE IF NOT EXISTS absence (
 id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES personnel(id),
 category TEXT NOT NULL CHECK(category IN ('annual_leave','other_leave','medical_absence','other')),
 starts_on TEXT NOT NULL, ends_on TEXT NOT NULL, basis_number TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'draft' CHECK(state IN ('draft','approved','closed')),
 CHECK(ends_on>=starts_on)
);
CREATE INDEX IF NOT EXISTS ix_absence_person_period ON absence(person_id,starts_on,ends_on);
CREATE TABLE IF NOT EXISTS duty (
 id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES personnel(id),
 duty_date TEXT NOT NULL, label TEXT NOT NULL, basis_number TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'planned' CHECK(state IN ('planned','completed','cancelled'))
);
CREATE INDEX IF NOT EXISTS ix_duty_person_date ON duty(person_id,duty_date);
CREATE TABLE IF NOT EXISTS asset (
 id INTEGER PRIMARY KEY, name TEXT NOT NULL, inventory_number TEXT UNIQUE NOT NULL,
 category TEXT NOT NULL, condition TEXT NOT NULL DEFAULT 'serviceable'
);
CREATE TABLE IF NOT EXISTS asset_issue (
 id INTEGER PRIMARY KEY, asset_id INTEGER NOT NULL REFERENCES asset(id),
 person_id INTEGER NOT NULL REFERENCES personnel(id), issued_on TEXT NOT NULL,
 returned_on TEXT, basis_number TEXT NOT NULL,
 CHECK(returned_on IS NULL OR returned_on>=issued_on)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_one_active_issue ON asset_issue(asset_id) WHERE returned_on IS NULL;
CREATE TABLE IF NOT EXISTS org_unit (
 id INTEGER PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
 parent_id INTEGER REFERENCES org_unit(id), active INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS staff_slot (
 id INTEGER PRIMARY KEY, unit_id INTEGER NOT NULL REFERENCES org_unit(id),
 code TEXT NOT NULL UNIQUE, title TEXT NOT NULL, grade TEXT NOT NULL DEFAULT '',
 authorized INTEGER NOT NULL DEFAULT 1 CHECK(authorized IN (0,1))
);
CREATE INDEX IF NOT EXISTS ix_staff_slot_unit ON staff_slot(unit_id);
CREATE TABLE IF NOT EXISTS hr_document (
 id INTEGER PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
 document_number TEXT NOT NULL UNIQUE, document_date TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'draft' CHECK(state IN ('draft','review','signed','registered','filed')),
 source_url TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS batch_duty (
 id INTEGER PRIMARY KEY, document_id INTEGER NOT NULL UNIQUE REFERENCES hr_document(id),
 label TEXT NOT NULL, starts_on TEXT NOT NULL, ends_on TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, CHECK(ends_on>=starts_on)
);
CREATE TABLE IF NOT EXISTS batch_duty_member (
 batch_id INTEGER NOT NULL REFERENCES batch_duty(id),
 person_id INTEGER NOT NULL REFERENCES personnel(id),
 PRIMARY KEY(batch_id,person_id)
);
CREATE TABLE IF NOT EXISTS data_access_catalog (
 code TEXT PRIMARY KEY, dataset_name TEXT NOT NULL, category TEXT NOT NULL,
 owner_section TEXT NOT NULL, handling_note TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS appointment (
 id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES personnel(id),
 slot_id INTEGER NOT NULL REFERENCES staff_slot(id),
 starts_on TEXT NOT NULL, ends_on TEXT,
 document_id INTEGER NOT NULL REFERENCES hr_document(id),
 CHECK(ends_on IS NULL OR ends_on>=starts_on)
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_one_slot_holder ON appointment(slot_id) WHERE ends_on IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ix_one_person_slot ON appointment(person_id) WHERE ends_on IS NULL;
"""


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connection(path) as db:
        db.executescript(SCHEMA)
        existing = {row['name'] for row in db.execute('PRAGMA table_info(personnel)')}
        for column in ('rank_label','specialty','service_since','profile_note'):
            if column not in existing:
                db.execute(f"ALTER TABLE personnel ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        db.executemany("""INSERT OR IGNORE INTO data_access_catalog
            (code,dataset_name,category,owner_section,handling_note)
            VALUES (?,?,?,?,?)""", [
            ('personnel','Картки персоналу','personal','personnel','Лише синтетичні дані; доступ за роллю'),
            ('events','Кадрові події','personal','personnel','Історія підстав зберігається'),
            ('duties','Адміністративна зайнятість','internal','duties','Без маршрутів, позицій і бойових завдань'),
            ('documents','Документи-основи','internal','documents','Номери лише DEMO; не офіційний архів'),
            ('assets','Номерне майно','internal','assets','Лише демонстраційне неозброєне майно'),
            ('roles','Ролі та аудит','restricted_metadata','roles','Тільки власник для зміни правил'),
        ])
        if not db.execute('SELECT 1 FROM personnel LIMIT 1').fetchone():
            db.executemany(
                'INSERT INTO personnel(display_name,service_number,status) VALUES (?,?,?)',
                [('Демо Особа А', 'DEMO-001', 'active'), ('Демо Особа Б', 'DEMO-002', 'leave')],
            )
        if not db.execute('SELECT 1 FROM asset LIMIT 1').fetchone():
            db.executemany('INSERT INTO asset(name,inventory_number,category) VALUES(?,?,?)', [
                ('Демо планшет', 'DEMO-ASSET-001', 'equipment'),
                ('Демо комплект', 'DEMO-ASSET-002', 'kit'),
            ])
        if not db.execute('SELECT 1 FROM org_unit LIMIT 1').fetchone():
            db.execute('INSERT INTO org_unit(code,name) VALUES(?,?)', ('DEMO-HQ', 'Демо управління'))
            db.execute('INSERT INTO org_unit(code,name,parent_id) VALUES(?,?,1)',
                       ('DEMO-HR', 'Демо кадровий підрозділ'))
            db.execute('INSERT INTO staff_slot(unit_id,code,title,grade) VALUES(?,?,?,?)',
                       (2, 'DEMO-POS-01', 'Демо посада', ''))


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


def _date(value: str) -> str:
    from datetime import date
    return date.fromisoformat(value).isoformat()


def _text(value: str, limit: int = 120) -> str:
    value = value.strip()
    if not value or len(value) > limit:
        raise ValueError('Required field missing or too long')
    return value


def add_person(path: Path, name: str, service_number: str) -> None:
    if not service_number.startswith('DEMO-'):
        raise ValueError('Only synthetic DEMO numbers are allowed')
    with connection(path) as db:
        db.execute('INSERT INTO personnel(display_name,service_number,status) VALUES(?,?,?)',
                   (_text(name), _text(service_number, 50), 'active'))


def add_absence(path: Path, person_id: int, category: str, start: str, end: str, basis: str) -> None:
    if category not in {'annual_leave', 'other_leave', 'medical_absence', 'other'}:
        raise ValueError('Invalid absence category')
    start, end = _date(start), _date(end)
    if end < start:
        raise ValueError('End before start')
    with connection(path) as db:
        if not db.execute('SELECT 1 FROM personnel WHERE id=?', (person_id,)).fetchone():
            raise ValueError('Unknown person')
        if db.execute('''SELECT 1 FROM absence WHERE person_id=? AND state!='closed'
                      AND starts_on<=? AND ends_on>=?''', (person_id, end, start)).fetchone():
            raise ValueError('Overlapping absence')
        db.execute('INSERT INTO absence(person_id,category,starts_on,ends_on,basis_number) VALUES(?,?,?,?,?)',
                   (person_id, category, start, end, _text(basis)))


def add_duty(path: Path, person_id: int, date: str, label: str, basis: str) -> None:
    date = _date(date)
    with connection(path) as db:
        if not db.execute('SELECT 1 FROM personnel WHERE id=?', (person_id,)).fetchone():
            raise ValueError('Unknown person')
        if db.execute('''SELECT 1 FROM absence WHERE person_id=? AND state='approved'
                      AND starts_on<=? AND ends_on>=?''', (person_id, date, date)).fetchone():
            raise ValueError('Approved absence conflicts with duty')
        if db.execute("""SELECT 1 FROM batch_duty_member m JOIN batch_duty b ON b.id=m.batch_id
            WHERE m.person_id=? AND b.starts_on<=? AND b.ends_on>=?""",
            (person_id,date,date)).fetchone():
            raise ValueError('Batch period conflicts with duty')
        db.execute('INSERT INTO duty(person_id,duty_date,label,basis_number) VALUES(?,?,?,?)',
                   (person_id, date, _text(label), _text(basis)))


def add_asset(path: Path, name: str, number: str, category: str) -> None:
    if not number.startswith('DEMO-'):
        raise ValueError('Only synthetic DEMO numbers are allowed')
    with connection(path) as db:
        db.execute('INSERT INTO asset(name,inventory_number,category) VALUES(?,?,?)',
                   (_text(name), _text(number, 80), _text(category, 50)))


def issue_asset(path: Path, asset_id: int, person_id: int, date: str, basis: str) -> None:
    with connection(path) as db:
        db.execute('INSERT INTO asset_issue(asset_id,person_id,issued_on,basis_number) VALUES(?,?,?,?)',
                   (asset_id, person_id, _date(date), _text(basis)))


def return_asset(path: Path, issue_id: int, date: str) -> None:
    with connection(path) as db:
        row = db.execute('SELECT issued_on,returned_on FROM asset_issue WHERE id=?', (issue_id,)).fetchone()
        if not row or row['returned_on'] or _date(date) < row['issued_on']:
            raise ValueError('Invalid asset return')
        db.execute('UPDATE asset_issue SET returned_on=? WHERE id=?', (_date(date), issue_id))


def _demo_code(value: str, limit: int = 80) -> str:
    value = _text(value, limit)
    if not value.startswith('DEMO-'):
        raise ValueError('Only synthetic DEMO identifiers are allowed')
    return value


def add_unit(path: Path, code: str, name: str, parent_id: int | None) -> None:
    with connection(path) as db:
        if parent_id is not None and not db.execute(
            'SELECT 1 FROM org_unit WHERE id=? AND active=1', (parent_id,)
        ).fetchone():
            raise ValueError('Unknown parent unit')
        db.execute('INSERT INTO org_unit(code,name,parent_id) VALUES(?,?,?)',
                   (_demo_code(code), _text(name), parent_id))


def add_slot(path: Path, unit_id: int, code: str, title: str, grade: str) -> None:
    with connection(path) as db:
        if not db.execute('SELECT 1 FROM org_unit WHERE id=? AND active=1', (unit_id,)).fetchone():
            raise ValueError('Unknown unit')
        db.execute('INSERT INTO staff_slot(unit_id,code,title,grade) VALUES(?,?,?,?)',
                   (unit_id, _demo_code(code), _text(title), grade.strip()[:60]))


def add_document(path: Path, kind: str, title: str, number: str, date: str) -> None:
    if kind not in {'order', 'report', 'referral', 'other'}:
        raise ValueError('Unsupported document kind')
    with connection(path) as db:
        db.execute('INSERT INTO hr_document(kind,title,document_number,document_date) VALUES(?,?,?,?)',
                   (kind, _text(title), _demo_code(number), _date(date)))


def advance_document(path: Path, document_id: int) -> None:
    transitions = {'draft': 'review', 'review': 'signed', 'signed': 'registered',
                   'registered': 'filed'}
    with connection(path) as db:
        row = db.execute('SELECT state FROM hr_document WHERE id=?', (document_id,)).fetchone()
        if not row or row['state'] not in transitions:
            raise ValueError('No next state')
        db.execute('UPDATE hr_document SET state=? WHERE id=?',
                   (transitions[row['state']], document_id))


def appoint(path: Path, person_id: int, slot_id: int, document_id: int, start: str) -> None:
    start = _date(start)
    with connection(path) as db:
        doc = db.execute('SELECT state,kind,document_date FROM hr_document WHERE id=?',
                         (document_id,)).fetchone()
        if not doc or doc['kind'] != 'order' or doc['state'] not in {'registered','filed'}:
            raise ValueError('A registered order is required')
        if doc['document_date'] > start:
            raise ValueError('Order date is later than appointment')
        if not db.execute('SELECT 1 FROM personnel WHERE id=?', (person_id,)).fetchone():
            raise ValueError('Unknown person')
        if not db.execute('SELECT 1 FROM staff_slot WHERE id=? AND authorized=1', (slot_id,)).fetchone():
            raise ValueError('Unknown or inactive slot')
        db.execute('INSERT INTO appointment(person_id,slot_id,document_id,starts_on) VALUES(?,?,?,?)',
                   (person_id, slot_id, document_id, start))


def update_profile(path: Path, person_id: int, rank_label: str, specialty: str,
                   service_since: str, profile_note: str) -> None:
    if service_since:
        service_since = _date(service_since)
    if len(profile_note) > 500:
        raise ValueError('Note too long')
    with connection(path) as db:
        result = db.execute("""UPDATE personnel SET rank_label=?,specialty=?,service_since=?,profile_note=?
             WHERE id=?""", (rank_label.strip()[:80], specialty.strip()[:80], service_since,
                            profile_note.strip(), person_id))
        if result.rowcount != 1:
            raise ValueError('Unknown person')


def create_batch_duty(path: Path, document_id: int, label: str, start: str,
                      end: str, person_ids: list[int]) -> int:
    """One registered demo document, one atomic personnel selection, no operational locations."""
    label, start, end = _text(label, 120), _date(start), _date(end)
    ids = list(person_ids)
    if end < start or not ids or len(ids) > 100 or len(set(ids)) != len(ids):
        raise ValueError('Choose 1–100 distinct people and a valid period')
    with connection(path) as db:
        doc = db.execute('SELECT kind,state,document_number FROM hr_document WHERE id=?',
                         (document_id,)).fetchone()
        if not doc or doc['kind'] != 'order' or doc['state'] not in ('registered','filed'):
            raise ValueError('A registered demo order is required')
        if db.execute('SELECT 1 FROM batch_duty WHERE document_id=?', (document_id,)).fetchone():
            raise ValueError('Document is already used for a batch')
        for person_id in ids:
            person = db.execute('SELECT status FROM personnel WHERE id=?', (person_id,)).fetchone()
            if not person or person['status'] != 'active':
                raise ValueError('Selection contains a person without active status')
            if db.execute("""SELECT 1 FROM absence WHERE person_id=? AND state='approved'
                AND starts_on<=? AND ends_on>=?""", (person_id, end, start)).fetchone():
                raise ValueError('Selection conflicts with approved absence')
            if db.execute("""SELECT 1 FROM duty WHERE person_id=? AND state='planned'
                AND duty_date BETWEEN ? AND ?""", (person_id, start, end)).fetchone():
                raise ValueError('Selection conflicts with planned duty')
            if db.execute("""SELECT 1 FROM batch_duty_member m JOIN batch_duty b ON b.id=m.batch_id
                WHERE m.person_id=? AND b.starts_on<=? AND b.ends_on>=?""",
                (person_id,end,start)).fetchone():
                raise ValueError('Selection conflicts with an existing batch period')
        batch_id = db.execute("""INSERT INTO batch_duty(document_id,label,starts_on,ends_on)
            VALUES(?,?,?,?)""", (document_id,label,start,end)).lastrowid
        db.executemany('INSERT INTO batch_duty_member(batch_id,person_id) VALUES(?,?)',
                       [(batch_id,pid) for pid in ids])
        db.executemany('''INSERT INTO duty(person_id,duty_date,label,basis_number)
            VALUES(?,?,?,?)''', [(pid,start,label,doc['document_number']) for pid in ids])
        return batch_id
