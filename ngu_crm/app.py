"""Local, synthetic-data preview; no authorization for real personnel processing."""
import os
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from .data import add_event, connection, init_db

ROOT = Path(__file__).resolve().parent.parent
DB = Path(os.environ.get('NGU_DEMO_DB', str(ROOT / 'demo.db')))
templates = Jinja2Templates(directory=str(ROOT / 'templates'))
app = FastAPI(title='НГУ — демонстраційний облік', docs_url=None, redoc_url=None, openapi_url=None)


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
    return page(request, 'dashboard', lang, counts=counts, recent=recent)


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


@app.get('/print/events', response_class=HTMLResponse)
def print_events(request: Request, lang: str = 'uk'):
    with connection(DB) as db:
        events = [dict(row) for row in db.execute('''
            SELECT e.*, p.display_name, p.service_number FROM events e
            JOIN personnel p ON p.id=e.person_id ORDER BY e.effective_date,e.id
        ''')]
    return page(request, 'print', lang, events=events)
