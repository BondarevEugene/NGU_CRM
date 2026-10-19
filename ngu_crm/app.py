"""Local, synthetic-data preview; no authorization for real personnel processing."""
import os
import secrets
import hmac
from urllib.parse import parse_qs
import csv
import io
from datetime import date
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

import sqlite3

from .data import (add_absence, add_asset, add_duty, add_event, add_person,
                   add_document, add_slot, add_unit, advance_document, appoint,
                   connection, init_db, issue_asset, return_asset, update_profile, create_batch_duty)
from .access import (SECTIONS, ACTIONS, section_for, allowed, save_role, session_secret,
                     hash_password, verify_password)
from .reports import staffing_summary, administrative_snapshot

ROOT = Path(__file__).resolve().parent.parent
DB = Path(os.environ.get('NGU_DEMO_DB', str(ROOT / 'demo.db')))
templates = Jinja2Templates(directory=str(ROOT / 'templates'))
app = FastAPI(title='НГУ — демонстраційний облік', docs_url=None, redoc_url=None, openapi_url=None)
app.mount('/static', StaticFiles(directory=str(ROOT / 'static')), name='static')


@app.on_event('startup')
def start():
    init_db(DB)


@app.middleware('http')
async def access_control(request: Request, call_next):
    if request.client is None or request.client.host not in {'127.0.0.1', '::1', 'testclient'}:
        return Response('Local demonstration only', status_code=403)
    path = request.url.path
    if path == '/login' or path.startswith('/static/'):
        return await call_next(request)
    user_id = request.session.get('user_id')
    with connection(DB) as db:
        user = db.execute('SELECT * FROM app_user WHERE id=?', (user_id,)).fetchone() if user_id else None
        if not user:
            return RedirectResponse('/login', status_code=303)
        section = 'dashboard' if path == '/logout' else section_for(path)
        if section is None:
            return Response('Not found', status_code=404)
        action = 'delete' if path.endswith('/delete') else ('edit' if request.method == 'POST' else 'view')
        if path != '/logout' and not allowed(db, user, section, action):
            return Response('Access denied', status_code=403)
        request.state.user = dict(user)
        request.state.permissions = {(s, a): allowed(db, user, s, a)
                                     for s in SECTIONS for a in ACTIONS}
    if request.method == 'POST':
        body = await request.body()
        token = parse_qs(body.decode('utf-8', 'replace')).get('_csrf', [''])[0]
        async def replay_body():
            return {'type': 'http.request', 'body': body, 'more_body': False}
        request._receive = replay_body
        if not token or not hmac.compare_digest(str(token), str(request.session.get('csrf', ''))):
            return Response('Invalid form token', status_code=403)
    response = await call_next(request)
    if request.method == 'POST' and response.status_code in (200, 201, 303):
        with connection(DB) as db:
            db.execute('INSERT INTO audit_event(actor_id,action,route) VALUES(?,?,?)',
                       (user_id, action, path))
    response.headers['Cache-Control'] = 'no-store'
    return response


app.add_middleware(SessionMiddleware, secret_key=session_secret(ROOT), same_site='strict', https_only=False)


@app.get('/login', response_class=HTMLResponse)
def login_page(request: Request):
    return templates.TemplateResponse(request, 'login.html', {'request': request})


@app.post('/login')
def login(request: Request, username: str = Form(...), password: str = Form(...)):
    with connection(DB) as db:
        user = db.execute('SELECT * FROM app_user WHERE username=?', (username.strip(),)).fetchone()
    if not user or not verify_password(password, user['password_hash']):
        return templates.TemplateResponse(request, 'login.html', {'request': request, 'error': True},
                                          status_code=401)
    request.session.clear()
    request.session['user_id'] = user['id']
    request.session['csrf'] = secrets.token_urlsafe(32)
    return RedirectResponse('/', status_code=303)


@app.post('/logout')
def logout(request: Request):
    request.session.clear()
    return RedirectResponse('/login', status_code=303)


def language(value: str) -> str:
    return value if value in {'uk', 'ru'} else 'uk'


def page(request: Request, view: str, lang: str, **kwargs):
    return templates.TemplateResponse(request, 'base.html', {
        'view': view, 'lang': language(lang), 'request': request,
        'permissions': request.state.permissions, 'current_user': request.state.user,
        'csrf_token': request.session['csrf'], **kwargs
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
def personnel(request: Request, lang: str = 'uk', status: str = '', q: str = '', page_number: int = 1):
    if status and status not in {'active', 'leave', 'transferred', 'excluded'}:
        raise HTTPException(400, 'Invalid filter')
    if len(q) > 80 or not 1 <= page_number <= 10000:
        raise HTTPException(400, 'Invalid search')
    term = '%' + q.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    where = "WHERE (?='' OR p.status=?) AND (?='' OR p.display_name LIKE ? ESCAPE '\\' OR p.service_number LIKE ? ESCAPE '\\')"
    args = (status, status, q, term, term)
    with connection(DB) as db:
        total = db.execute('SELECT count(*) FROM personnel p ' + where, args).fetchone()[0]
        rows = [dict(row) for row in db.execute('''SELECT p.*,s.title position_title,u.name unit_name,
          (SELECT count(*) FROM duty d WHERE d.person_id=p.id AND d.state='planned') pending_duties
          FROM personnel p LEFT JOIN appointment ap ON ap.person_id=p.id AND ap.ends_on IS NULL
          LEFT JOIN staff_slot s ON s.id=ap.slot_id LEFT JOIN org_unit u ON u.id=s.unit_id
          ''' + where + ' ORDER BY p.id LIMIT 100 OFFSET ?', (*args, (page_number-1)*100))]
    return page(request, 'personnel', lang, rows=rows, status_filter=status, search_query=q,
                total=total, page_number=page_number)


@app.get('/personnel/{person_id}', response_class=HTMLResponse)
def person(request: Request, person_id: int, lang: str = 'uk'):
    with connection(DB) as db:
        found = db.execute('SELECT * FROM personnel WHERE id=?', (person_id,)).fetchone()
        if not found:
            raise HTTPException(404)
        events = [dict(row) for row in db.execute(
            'SELECT * FROM events WHERE person_id=? ORDER BY effective_date DESC,id DESC', (person_id,)
        )]
        absence_rows = [dict(r) for r in db.execute(
            'SELECT * FROM absence WHERE person_id=? ORDER BY starts_on DESC LIMIT 12', (person_id,))]
        duty_rows = [dict(r) for r in db.execute(
            'SELECT * FROM duty WHERE person_id=? ORDER BY duty_date DESC LIMIT 12', (person_id,))]
        assignment_rows = [dict(r) for r in db.execute("""SELECT a.starts_on,a.ends_on,s.title,u.name unit_name,
            d.document_number FROM appointment a JOIN staff_slot s ON s.id=a.slot_id
            JOIN org_unit u ON u.id=s.unit_id JOIN hr_document d ON d.id=a.document_id
            WHERE a.person_id=? ORDER BY a.starts_on DESC LIMIT 12""", (person_id,))]
        asset_rows = [dict(r) for r in db.execute("""SELECT a.name,a.inventory_number,i.issued_on,i.returned_on
            FROM asset_issue i JOIN asset a ON a.id=i.asset_id WHERE i.person_id=?
            ORDER BY i.issued_on DESC LIMIT 12""", (person_id,))]
    return page(request, 'person', lang, person=dict(found), events=events, absence_rows=absence_rows,
                duty_rows=duty_rows, assignment_rows=assignment_rows, asset_rows=asset_rows)


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
        if not conflict:
            conflict = db.execute("""SELECT 1 FROM batch_duty_member m JOIN batch_duty b ON b.id=m.batch_id
                WHERE m.person_id=? AND b.starts_on<=? AND b.ends_on>=?""",
                (row['person_id'],row['ends_on'],row['starts_on'])).fetchone()
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


@app.get('/management', response_class=HTMLResponse)
def management(request: Request, lang: str = 'uk', on_date: str = ''):
    day = on_date or date.today().isoformat()
    try:
        personnel_rows, personnel_totals = administrative_snapshot(DB, day)
    except ValueError as exc:
        raise HTTPException(400, 'Invalid date') from exc
    units, staffing_totals = staffing_summary(DB)
    with connection(DB) as db:
        pending = db.execute("SELECT count(*) FROM hr_document WHERE state IN ('draft','review')").fetchone()[0]
        registered = db.execute("SELECT count(*) FROM hr_document WHERE state IN ('registered','filed')").fetchone()[0]
    return page(request, 'management', lang, day=day, personnel_rows=personnel_rows,
                personnel_totals=personnel_totals, units=units,
                staffing_totals=staffing_totals, pending=pending, registered=registered)


@app.get('/management/export.csv')
def management_export(on_date: str = ''):
    day = on_date or date.today().isoformat()
    try:
        rows, _ = administrative_snapshot(DB, day)
    except ValueError as exc:
        raise HTTPException(400, 'Invalid date') from exc
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['DEMO ID','DEMO number','Name','Card status','Approved absence',
                     'Basis','Unit','Position'])
    for row in rows:
        # A leading apostrophe prevents spreadsheet formula execution on open.
        safe = lambda value: "'" + str(value) if str(value).startswith(('=','+','-','@')) else value
        writer.writerow([safe(row['id']),safe(row['service_number']),safe(row['display_name']),
                         safe(row['status']),safe(row['absence_category'] or ''),
                         safe(row['absence_basis'] or ''),safe(row['unit_name'] or ''),
                         safe(row['position_title'] or '')])
    return Response('\ufeff' + output.getvalue(), media_type='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename="ngu_demo_management.csv"'})

@app.post('/personnel/{person_id}/profile')
def profile(person_id: int, lang: str = Form('uk'), rank_label: str = Form(''),
            specialty: str = Form(''), service_since: str = Form(''), profile_note: str = Form('')):
    return _save(update_profile, f'/personnel/{person_id}', lang, person_id,
                 rank_label, specialty, service_since, profile_note)


@app.get('/admin/roles', response_class=HTMLResponse)
def roles(request: Request, lang: str = 'uk'):
    with connection(DB) as db:
        role_rows = [dict(r) for r in db.execute('SELECT * FROM app_role ORDER BY name')]
        users = [dict(r) for r in db.execute('''SELECT u.id,u.username,u.is_owner,r.name role_name
            FROM app_user u LEFT JOIN app_role r ON r.id=u.role_id ORDER BY u.id''')]
        permissions = {(r['role_id'], r['section'], r['action']) for r in
                       db.execute('SELECT * FROM role_permission')}
        log = [dict(r) for r in db.execute('''SELECT a.*,u.username FROM audit_event a
            JOIN app_user u ON u.id=a.actor_id ORDER BY a.id DESC LIMIT 30''')]
    return page(request, 'roles', lang, roles=role_rows, users=users, role_permissions=permissions,
                sections=SECTIONS, actions=ACTIONS, audit_log=log)


@app.post('/admin/roles')
async def create_role(request: Request, name: str = Form(...), lang: str = Form('uk')):
    values = (await request.form()).getlist('permission')
    return _save(save_role, '/admin/roles', lang, name, values)


@app.post('/admin/users')
def create_user(username: str = Form(...), password: str = Form(...),
                role_id: int = Form(...), lang: str = Form('uk')):
    username = username.strip()
    if not (3 <= len(username) <= 50) or not username.replace('_', '').isalnum():
        raise HTTPException(400, 'Invalid username')
    try:
        digest = hash_password(password)
        with connection(DB) as db:
            if not db.execute('SELECT 1 FROM app_role WHERE id=?', (role_id,)).fetchone():
                raise ValueError('Unknown role')
            db.execute('INSERT INTO app_user(username,password_hash,role_id) VALUES(?,?,?)',
                       (username, digest, role_id))
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(f'/admin/roles?lang={language(lang)}', status_code=303)


def validated_permissions(values):
    parsed = set()
    for item in values:
        if ':' not in item:
            raise HTTPException(400, 'Invalid permission')
        sec, act = item.split(':', 1)
        if sec not in SECTIONS or act not in ACTIONS:
            raise HTTPException(400, 'Invalid permission')
        parsed.add((sec, act))
    return parsed


@app.post('/admin/roles/{role_id}/permissions')
async def update_role(request: Request, role_id: int, lang: str = Form('uk')):
    parsed = validated_permissions((await request.form()).getlist('permission'))
    with connection(DB) as db:
        if not db.execute('SELECT 1 FROM app_role WHERE id=?', (role_id,)).fetchone():
            raise HTTPException(404)
        db.execute('DELETE FROM role_permission WHERE role_id=?', (role_id,))
        db.executemany('INSERT INTO role_permission(role_id,section,action) VALUES(?,?,?)',
                       [(role_id,s,a) for s,a in sorted(parsed)])
    return RedirectResponse(f'/admin/roles?lang={language(lang)}', status_code=303)


@app.post('/admin/roles/{role_id}/delete')
def delete_role(role_id: int, lang: str = Form('uk')):
    with connection(DB) as db:
        if db.execute('SELECT 1 FROM app_user WHERE role_id=?', (role_id,)).fetchone():
            raise HTTPException(400, 'Role is assigned to users')
        db.execute('DELETE FROM role_permission WHERE role_id=?', (role_id,))
        result = db.execute('DELETE FROM app_role WHERE id=?', (role_id,))
        if result.rowcount != 1:
            raise HTTPException(404)
    return RedirectResponse(f'/admin/roles?lang={language(lang)}', status_code=303)

@app.post('/personnel/{person_id}/delete')
def remove_person(person_id: int, lang: str = Form('uk')):
    with connection(DB) as db:
        for table in ('events','absence','duty','asset_issue','appointment'):
            if db.execute(f'SELECT 1 FROM {table} WHERE person_id=?', (person_id,)).fetchone():
                raise HTTPException(400, 'Card has related records and cannot be deleted')
        result = db.execute('DELETE FROM personnel WHERE id=?', (person_id,))
        if result.rowcount != 1:
            raise HTTPException(404)
    return RedirectResponse(f'/personnel?lang={language(lang)}', status_code=303)


@app.post('/absences/{absence_id}/delete')
def remove_absence(absence_id: int, lang: str = Form('uk')):
    with connection(DB) as db:
        result = db.execute("DELETE FROM absence WHERE id=? AND state='draft'", (absence_id,))
        if result.rowcount != 1:
            raise HTTPException(400, 'Only drafts can be deleted')
    return RedirectResponse(f'/absences?lang={language(lang)}', status_code=303)


@app.post('/duties/{duty_id}/delete')
def remove_duty(duty_id: int, lang: str = Form('uk')):
    with connection(DB) as db:
        result = db.execute("DELETE FROM duty WHERE id=? AND state='planned'", (duty_id,))
        if result.rowcount != 1:
            raise HTTPException(400, 'Only planned entries can be deleted')
    return RedirectResponse(f'/duties?lang={language(lang)}', status_code=303)


@app.post('/assets/{asset_id}/delete')
def remove_asset(asset_id: int, lang: str = Form('uk')):
    with connection(DB) as db:
        if db.execute('SELECT 1 FROM asset_issue WHERE asset_id=?', (asset_id,)).fetchone():
            raise HTTPException(400, 'Asset has issue history')
        result = db.execute('DELETE FROM asset WHERE id=?', (asset_id,))
        if result.rowcount != 1:
            raise HTTPException(404)
    return RedirectResponse(f'/assets?lang={language(lang)}', status_code=303)


@app.post('/documents/{document_id}/delete')
def remove_document(document_id: int, lang: str = Form('uk')):
    with connection(DB) as db:
        if db.execute('SELECT 1 FROM appointment WHERE document_id=?', (document_id,)).fetchone():
            raise HTTPException(400, 'Document is linked to appointment')
        result = db.execute("DELETE FROM hr_document WHERE id=? AND state='draft'", (document_id,))
        if result.rowcount != 1:
            raise HTTPException(400, 'Only unlinked drafts can be deleted')
    return RedirectResponse(f'/documents?lang={language(lang)}', status_code=303)


@app.post('/structure/slots/{slot_id}/delete')
def remove_slot(slot_id: int, lang: str = Form('uk')):
    with connection(DB) as db:
        if db.execute('SELECT 1 FROM appointment WHERE slot_id=?', (slot_id,)).fetchone():
            raise HTTPException(400, 'Slot has appointment history')
        result = db.execute('DELETE FROM staff_slot WHERE id=?', (slot_id,))
        if result.rowcount != 1:
            raise HTTPException(404)
    return RedirectResponse(f'/structure?lang={language(lang)}', status_code=303)


@app.get('/duties/batches', response_class=HTMLResponse)
def duty_batches(request: Request, lang: str = 'uk'):
    with connection(DB) as db:
        batches = [dict(r) for r in db.execute('''SELECT b.*,d.document_number,
            count(m.person_id) members FROM batch_duty b
            JOIN hr_document d ON d.id=b.document_id LEFT JOIN batch_duty_member m ON m.batch_id=b.id
            GROUP BY b.id ORDER BY b.id DESC''')]
        people = [dict(r) for r in db.execute("SELECT id,display_name,service_number FROM personnel WHERE status='active' ORDER BY display_name")]
        documents = [dict(r) for r in db.execute('''SELECT d.id,d.document_number FROM hr_document d
            WHERE d.kind='order' AND d.state IN ('registered','filed')
            AND NOT EXISTS(SELECT 1 FROM batch_duty b WHERE b.document_id=d.id)
            ORDER BY d.id DESC''')]
    return page(request, 'duty_batches', lang, batches=batches, people=people, documents=documents)


@app.post('/duties/batches')
async def new_duty_batch(request: Request, document_id: int = Form(...), label: str = Form(...),
                         starts_on: str = Form(...), ends_on: str = Form(...), lang: str = Form('uk')):
    values = (await request.form()).getlist('person_id')
    try:
        if not values or len(values) > 100 or any(not v.isdecimal() for v in values):
            raise ValueError('Choose 1–100 valid cards')
        create_batch_duty(DB, document_id, label, starts_on, ends_on, [int(v) for v in values])
    except (ValueError, sqlite3.IntegrityError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(f'/duties/batches?lang={language(lang)}', status_code=303)


@app.get('/catalog', response_class=HTMLResponse)
def data_catalog(request: Request, lang: str = 'uk'):
    with connection(DB) as db:
        rows = [dict(r) for r in db.execute('SELECT * FROM data_access_catalog ORDER BY code')]
    return page(request, 'catalog', lang, catalog_rows=rows)


@app.get('/duties/batches/{batch_id}', response_class=HTMLResponse)
def duty_batch_detail(request: Request, batch_id: int, lang: str = 'uk'):
    with connection(DB) as db:
        row = db.execute('''SELECT b.*,d.document_number FROM batch_duty b
            JOIN hr_document d ON d.id=b.document_id WHERE b.id=?''', (batch_id,)).fetchone()
        if not row:
            raise HTTPException(404)
        members = [dict(r) for r in db.execute('''SELECT p.id,p.display_name,p.service_number
            FROM batch_duty_member m JOIN personnel p ON p.id=m.person_id
            WHERE m.batch_id=? ORDER BY p.display_name''', (batch_id,))]
    return page(request, 'duty_batch_detail', lang, batch=dict(row), members=members)
