"""Local, synthetic-data preview; no authorization for real personnel processing."""
import os
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

import sqlite3

from .data import (add_absence, add_asset, add_duty, add_event, add_person,
                   add_document, add_slot, add_unit, advance_document, appoint,
                   connection, init_db, issue_asset, return_asset)

ROOT = Path(__file__).resolve().parent.parent
DB = Path(os.environ.get('NGU_DEMO_DB', str(ROOT / 'demo.db')))
templates = Jinja2Templates(directory=str(ROOT / 'templates'))
app = FastAPI(title='НГУ — демонстраційний облік', docs_url=None, redoc_url=None, openapi_url=None)
app.mount('/static', StaticFiles(directory=str(ROOT / 'static')), name='static')


@app.on_event('startup')
def start():
    init_db(DB)


@app.middleware('http')
async def local_only(request: Request, call_next):
    if request.client is None or request.client.host not in {'127.0.0.1', '::1', 'testclient'}:
        raise HTTPException(403, 'Local demonstration only')
    return await call_next(request)


def language(value: str) -> str:
    return value if value in {'uk', 'ru'} else 'uk'


def page(request: Request, view: str, lang: str, **kwargs):
    return templates.TemplateResponse(request, 'base.html', {
        'view': view, 'lang': language(lang), 'request': request, **kwargs
    })


@app.get('/', response_class=HTMLResponse)
def dashboard(request: Request, lang: str = 'uk'):
    with connection(DB) as db:
        counts = {row['status']: row['total'] for row in db.execute(
            'SELECT status, count(*) total FROM personnel GROUP BY status'
        )}
        recent = [dict(row) for row in db.execute('''
            SELECT e.*, p.display_name FROM events e JOIN personnel p ON p.id=e.person_id
            ORDER BY e.id DESC LIMIT 10
        ''')]
        hr = {key: db.execute(sql).fetchone()[0] for key, sql in {
            'absences': "SELECT count(*) FROM absence WHERE state!='closed'",
            'duties': "SELECT count(*) FROM duty WHERE state='planned'",
            'issued': 'SELECT count(*) FROM asset_issue WHERE returned_on IS NULL',
        }.items()}
    return page(request, 'dashboard', lang, counts=counts, recent=recent, hr=hr)


@app.get('/personnel', response_class=HTMLResponse)
def personnel(request: Request, lang: str = 'uk', status: str = ''):
    if status and status not in {'active', 'leave', 'transferred', 'excluded'}:
        raise HTTPException(400, 'Invalid filter')
    with connection(DB) as db:
        rows = [dict(row) for row in db.execute(
            'SELECT * FROM personnel WHERE (? = \'\' OR status=?) ORDER BY id', (status, status)
        )]
    return page(request, 'personnel', lang, rows=rows, status_filter=status)


@app.get('/personnel/{person_id}', response_class=HTMLResponse)
def person(request: Request, person_id: int, lang: str = 'uk'):
    with connection(DB) as db:
        found = db.execute('SELECT * FROM personnel WHERE id=?', (person_id,)).fetchone()
        if not found:
            raise HTTPException(404)
        events = [dict(row) for row in db.execute(
            'SELECT * FROM events WHERE person_id=? ORDER BY effective_date DESC,id DESC', (person_id,)
        )]
    return page(request, 'person', lang, person=dict(found), events=events)


@app.post('/personnel/{person_id}/events')
def event(person_id: int, lang: str = Form('uk'), kind: str = Form(...),
          effective_date: str = Form(...), basis_number: str = Form(...), note: str = Form('')):
    try:
        add_event(DB, person_id, kind, effective_date, basis_number, note)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(f'/personnel/{person_id}?lang={language(lang)}', status_code=303)


def _save(action, target: str, lang: str, *args):
    try:
        action(DB, *args)
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(f'{target}?lang={language(lang)}', status_code=303)


@app.post('/personnel')
def create_person(lang: str = Form('uk'), name: str = Form(...), number: str = Form(...)):
    return _save(add_person, '/personnel', lang, name, number)


@app.get('/absences', response_class=HTMLResponse)
def absences(request: Request, lang: str = 'uk'):
    with connection(DB) as db:
        rows = [dict(r) for r in db.execute('''SELECT a.*,p.display_name FROM absence a
                    JOIN personnel p ON p.id=a.person_id ORDER BY a.starts_on DESC,a.id DESC''')]
        people = [dict(r) for r in db.execute('SELECT id,display_name FROM personnel ORDER BY id')]
    return page(request, 'absences', lang, rows=rows, people=people)


@app.post('/absences')
def create_absence(lang: str = Form('uk'), person_id: int = Form(...), category: str = Form(...),
                   starts_on: str = Form(...), ends_on: str = Form(...), basis_number: str = Form(...)):
    return _save(add_absence, '/absences', lang, person_id, category, starts_on, ends_on, basis_number)


@app.post('/absences/{absence_id}/approve')
def approve_absence(absence_id: int, lang: str = Form('uk')):
    with connection(DB) as db:
        row = db.execute('SELECT * FROM absence WHERE id=? AND state=\'draft\'', (absence_id,)).fetchone()
        if not row:
            raise HTTPException(400, 'Draft not found')
        conflict = db.execute('''SELECT 1 FROM duty WHERE person_id=? AND state='planned'
                   AND duty_date BETWEEN ? AND ?''', (row['person_id'],row['starts_on'],row['ends_on'])).fetchone()
        if conflict:
            raise HTTPException(400, 'Planned duty conflicts with absence')
        db.execute("UPDATE absence SET state='approved' WHERE id=?", (absence_id,))
    return RedirectResponse(f'/absences?lang={language(lang)}', status_code=303)


@app.get('/duties', response_class=HTMLResponse)
def duties(request: Request, lang: str = 'uk'):
    with connection(DB) as db:
        rows = [dict(r) for r in db.execute('''SELECT d.*,p.display_name FROM duty d
                    JOIN personnel p ON p.id=d.person_id ORDER BY d.duty_date DESC,d.id DESC''')]
        people = [dict(r) for r in db.execute('SELECT id,display_name FROM personnel ORDER BY id')]
    return page(request, 'duties', lang, rows=rows, people=people)


@app.post('/duties')
def create_duty(lang: str = Form('uk'), person_id: int = Form(...), duty_date: str = Form(...),
                label: str = Form(...), basis_number: str = Form(...)):
    return _save(add_duty, '/duties', lang, person_id, duty_date, label, basis_number)


@app.get('/assets', response_class=HTMLResponse)
def assets(request: Request, lang: str = 'uk'):
    with connection(DB) as db:
        rows = [dict(r) for r in db.execute('''SELECT a.*,i.id issue_id,i.issued_on,p.display_name
              FROM asset a LEFT JOIN asset_issue i ON i.asset_id=a.id AND i.returned_on IS NULL
              LEFT JOIN personnel p ON p.id=i.person_id ORDER BY a.id''')]
        people = [dict(r) for r in db.execute('SELECT id,display_name FROM personnel ORDER BY id')]
    return page(request, 'assets', lang, rows=rows, people=people)


@app.post('/assets')
def create_asset(lang: str = Form('uk'), name: str = Form(...), number: str = Form(...),
                 category: str = Form(...)):
    return _save(add_asset, '/assets', lang, name, number, category)


@app.post('/assets/issue')
def create_issue(lang: str = Form('uk'), asset_id: int = Form(...), person_id: int = Form(...),
                 issued_on: str = Form(...), basis_number: str = Form(...)):
    return _save(issue_asset, '/assets', lang, asset_id, person_id, issued_on, basis_number)


@app.post('/assets/{issue_id}/return')
def close_issue(issue_id: int, lang: str = Form('uk'), returned_on: str = Form(...)):
    return _save(return_asset, '/assets', lang, issue_id, returned_on)


@app.get('/print/events', response_class=HTMLResponse)
def print_events(request: Request, lang: str = 'uk'):
    with connection(DB) as db:
        events = [dict(row) for row in db.execute('''
            SELECT e.*, p.display_name, p.service_number FROM events e
            JOIN personnel p ON p.id=e.person_id ORDER BY e.effective_date,e.id
        ''')]
    return page(request, 'print', lang, events=events)


@app.get('/print/{kind}', response_class=HTMLResponse)
def print_register(request: Request, kind: str, lang: str = 'uk'):
    queries = {
        'absences': ("SELECT p.display_name person, a.category description, a.starts_on date, "
                     "a.ends_on detail, a.basis_number basis FROM absence a "
                     "JOIN personnel p ON p.id=a.person_id ORDER BY a.starts_on,a.id"),
        'duties': ("SELECT p.display_name person, d.label description, d.duty_date date, "
                   "d.state detail, d.basis_number basis FROM duty d "
                   "JOIN personnel p ON p.id=d.person_id ORDER BY d.duty_date,d.id"),
        'assets': ("SELECT a.name || ' · ' || a.inventory_number person, "
                   "p.display_name description, i.issued_on date, "
                   "coalesce(i.returned_on,'—') detail, i.basis_number basis FROM asset_issue i "
                   "JOIN asset a ON a.id=i.asset_id JOIN personnel p ON p.id=i.person_id "
                   "ORDER BY i.issued_on,i.id"),
    }
    if kind not in queries:
        raise HTTPException(404)
    with connection(DB) as db:
        rows = [dict(r) for r in db.execute(queries[kind])]
    return page(request, 'print_register', lang, rows=rows, kind=kind)


@app.get('/structure', response_class=HTMLResponse)
def structure(request: Request, lang: str = 'uk'):
    with connection(DB) as db:
        units = [dict(r) for r in db.execute('''SELECT u.*,parent.name parent_name,
            count(DISTINCT s.id) slots, count(DISTINCT a.id) occupied
            FROM org_unit u LEFT JOIN org_unit parent ON parent.id=u.parent_id
            LEFT JOIN staff_slot s ON s.unit_id=u.id AND s.authorized=1
            LEFT JOIN appointment a ON a.slot_id=s.id AND a.ends_on IS NULL
            GROUP BY u.id ORDER BY u.code''')]
        slots = [dict(r) for r in db.execute('''SELECT s.*,u.name unit_name,p.display_name holder
            FROM staff_slot s JOIN org_unit u ON u.id=s.unit_id
            LEFT JOIN appointment a ON a.slot_id=s.id AND a.ends_on IS NULL
            LEFT JOIN personnel p ON p.id=a.person_id ORDER BY u.code,s.code''')]
        people = [dict(r) for r in db.execute('''SELECT p.id,p.display_name FROM personnel p
            WHERE NOT EXISTS(SELECT 1 FROM appointment a WHERE a.person_id=p.id AND a.ends_on IS NULL)
            ORDER BY p.id''')]
        orders = [dict(r) for r in db.execute('''SELECT id,document_number FROM hr_document
            WHERE kind='order' AND state IN ('registered','filed') ORDER BY id DESC''')]
    return page(request, 'structure', lang, units=units, slots=slots, people=people, orders=orders)


@app.post('/structure/units')
def create_unit(lang: str = Form('uk'), code: str = Form(...), name: str = Form(...),
                parent_id: str = Form('')):
    return _save(add_unit, '/structure', lang, code, name, int(parent_id) if parent_id else None)


@app.post('/structure/slots')
def create_slot(lang: str = Form('uk'), unit_id: int = Form(...), code: str = Form(...),
                title: str = Form(...), grade: str = Form('')):
    return _save(add_slot, '/structure', lang, unit_id, code, title, grade)


@app.post('/structure/appointments')
def create_appointment(lang: str = Form('uk'), person_id: int = Form(...),
                       slot_id: int = Form(...), document_id: int = Form(...),
                       starts_on: str = Form(...)):
    return _save(appoint, '/structure', lang, person_id, slot_id, document_id, starts_on)


@app.get('/documents', response_class=HTMLResponse)
def documents(request: Request, lang: str = 'uk'):
    with connection(DB) as db:
        rows = [dict(r) for r in db.execute('SELECT * FROM hr_document ORDER BY document_date DESC,id DESC')]
    return page(request, 'documents', lang, rows=rows)


@app.post('/documents')
def create_document(lang: str = Form('uk'), kind: str = Form(...), title: str = Form(...),
                    number: str = Form(...), date: str = Form(...)):
    return _save(add_document, '/documents', lang, kind, title, number, date)


@app.post('/documents/{document_id}/advance')
def next_document_state(document_id: int, lang: str = Form('uk')):
    return _save(advance_document, '/documents', lang, document_id)
