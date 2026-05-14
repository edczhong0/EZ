from flask import Flask, request, jsonify, Response, stream_with_context, session
from flask_cors import CORS
import requests
from requests.auth import HTTPBasicAuth
from datetime import datetime, timedelta
import numpy as np
from statsmodels.tsa.arima.model import ARIMA
import sqlite3
import re
import os
import feedparser
import anthropic
import time
import imaplib
import email as email_lib
from email.header import decode_header as decode_email_header
import secrets
import smtplib
import json
import stripe
from email.mime.text import MIMEText
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from flask import send_from_directory, send_file
import pathlib
import shutil
import mimetypes
import subprocess
import hashlib
import tempfile

DB_PATH       = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'contacts.db')
USERS_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'users.db')
UPLOADS_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
PRODUCTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'products.json')
ALLOWED_EXTS  = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'tiff', 'tif', 'heic', 'heif',
                 'mp4', 'mov', 'avi', 'mkv', 'webm', 'm4v'}
os.makedirs(UPLOADS_DIR, exist_ok=True)
ANTHROPIC_API_KEY = os.environ.get('ANTHROPIC_API_KEY', '')

FEEDS = [
    ('Reuters',    'https://feeds.reuters.com/reuters/topNews'),
    ('TechCrunch', 'https://techcrunch.com/feed/'),
    ('Bloomberg',  'https://feeds.bloomberg.com/markets/news.rss'),
]

_news_cache = {'brief': None, 'sources': [], 'ts': 0, 'cached': False}
NEWS_CACHE_TTL = 30 * 60  # 30 minutes

def _load_email_accounts():
    accounts = []
    for i in range(1, 10):
        name = os.environ.get(f'EMAIL_{i}_NAME')
        if not name:
            break
        accounts.append({
            'name': name,
            'host': os.environ.get(f'EMAIL_{i}_HOST', ''),
            'user': os.environ.get(f'EMAIL_{i}_USER', ''),
            'pass': os.environ.get(f'EMAIL_{i}_PASS', ''),
        })
    return accounts

EMAIL_ACCOUNTS = _load_email_accounts()
EMAIL_KEYWORDS = ('email', 'inbox', 'mail', 'message', 'unread', 'sender', 'subject')

FRED_API_KEY = os.environ.get('FRED_API_KEY', '')
TWILIO_SID   = os.environ.get('TWILIO_SID', '')
TWILIO_TOKEN = os.environ.get('TWILIO_TOKEN', '')
TWILIO_FROM  = os.environ.get('TWILIO_FROM', '')

stripe.api_key  = os.environ.get('STRIPE_SECRET_KEY', '')
STRIPE_PUB_KEY  = os.environ.get('STRIPE_PUBLISHABLE_KEY', '')

app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))
CORS(app, supports_credentials=True, origins='http://localhost:5001')


def get_users_db():
    conn = sqlite3.connect(USERS_DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_users_db():
    conn = get_users_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            email          TEXT UNIQUE NOT NULL,
            password_hash  TEXT NOT NULL,
            created_at     TEXT NOT NULL,
            reset_token    TEXT,
            reset_expires  TEXT,
            is_admin       INTEGER DEFAULT 0,
            credit_balance INTEGER DEFAULT 500
        )
    ''')
    for col, definition in [
        ('is_admin',       'INTEGER DEFAULT 0'),
        ('credit_balance', 'INTEGER DEFAULT 0'),
    ]:
        try:
            conn.execute(f'ALTER TABLE users ADD COLUMN {col} {definition}')
        except Exception:
            pass
    conn.execute('''
        CREATE TABLE IF NOT EXISTS payments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            email      TEXT NOT NULL,
            product_id INTEGER NOT NULL,
            session_id TEXT,
            paid_at    TEXT,
            UNIQUE(email, product_id)
        )
    ''')
    conn.execute('''
        CREATE TABLE IF NOT EXISTS comments (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            email      TEXT NOT NULL,
            product_id INTEGER NOT NULL,
            body       TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
    ''')
    conn.commit()
    conn.close()


init_users_db()


def send_reset_email(to_email, reset_link):
    acct = EMAIL_ACCOUNTS[0] if EMAIL_ACCOUNTS else None
    if not acct:
        return False
    smtp_hosts = {
        'imap.gmail.com':      ('smtp.gmail.com', 587),
        'imap.mail.yahoo.com': ('smtp.mail.yahoo.com', 587),
        'imap.mail.me.com':    ('smtp.mail.me.com', 587),
    }
    smtp_host, smtp_port = smtp_hosts.get(acct['host'], ('smtp.gmail.com', 587))
    msg = MIMEText(f'Click the link below to reset your password:\n\n{reset_link}\n\nThis link expires in 1 hour.')
    msg['Subject'] = 'Password Reset Request'
    msg['From']    = acct['user']
    msg['To']      = to_email
    try:
        with smtplib.SMTP(smtp_host, smtp_port) as s:
            s.starttls()
            s.login(acct['user'], acct['pass'])
            s.sendmail(acct['user'], [to_email], msg.as_string())
        return True
    except Exception:
        return False


# ── Auth routes ──

@app.route('/auth/me')
def auth_me():
    if 'user' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    conn = get_users_db()
    row  = conn.execute('SELECT is_admin FROM users WHERE email=?', (session['user'],)).fetchone()
    conn.close()
    is_admin = bool(row['is_admin']) if row else False
    return jsonify({'email': session['user'], 'is_admin': is_admin})


@app.route('/auth/register', methods=['POST'])
def auth_register():
    data     = request.json or {}
    email    = data.get('email', '').strip().lower()
    password = data.get('password', '')
    if not re.match(r'^[^@]+@[^@]+\.[^@]+$', email):
        return jsonify({'error': 'Invalid email address'}), 400
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    conn = get_users_db()
    count    = conn.execute('SELECT COUNT(*) FROM users').fetchone()[0]
    is_admin = 1 if count == 0 else 0
    try:
        conn.execute(
            'INSERT INTO users (email, password_hash, created_at, is_admin, credit_balance) VALUES (?, ?, ?, ?, ?)',
            (email, generate_password_hash(password, method='pbkdf2:sha256'), datetime.utcnow().isoformat(), is_admin, 500)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return jsonify({'error': 'Email already registered'}), 409
    conn.close()
    session['user'] = email
    return jsonify({'ok': True, 'email': email, 'is_admin': bool(is_admin)})


@app.route('/auth/login', methods=['POST'])
def auth_login():
    data     = request.json or {}
    email    = data.get('email', '').strip().lower()
    password = data.get('password', '')
    conn     = get_users_db()
    row      = conn.execute('SELECT * FROM users WHERE email = ?', (email,)).fetchone()
    conn.close()
    if not row or not check_password_hash(row['password_hash'], password):
        return jsonify({'error': 'Invalid email or password'}), 401
    session['user'] = email
    return jsonify({'ok': True, 'email': email})


@app.route('/auth/logout', methods=['POST'])
def auth_logout():
    session.clear()
    return jsonify({'ok': True})


@app.route('/auth/forgot', methods=['POST'])
def auth_forgot():
    email = (request.json or {}).get('email', '').strip().lower()
    conn  = get_users_db()
    row   = conn.execute('SELECT id FROM users WHERE email = ?', (email,)).fetchone()
    if row:
        token   = secrets.token_urlsafe(32)
        expires = (datetime.utcnow() + timedelta(hours=1)).isoformat()
        conn.execute('UPDATE users SET reset_token=?, reset_expires=? WHERE email=?',
                     (token, expires, email))
        conn.commit()
        reset_link = f'http://localhost:5001/login.html?reset={token}'
        sent = send_reset_email(email, reset_link)
        conn.close()
        if not sent:
            # No SMTP configured — return link directly (dev mode)
            return jsonify({'ok': True, 'reset_link': reset_link, 'note': 'Email not configured; use this link directly'})
    else:
        conn.close()
    return jsonify({'ok': True, 'message': 'If that email exists, a reset link has been sent'})


@app.route('/auth/reset', methods=['POST'])
def auth_reset():
    data     = request.json or {}
    token    = data.get('token', '')
    password = data.get('password', '')
    if len(password) < 6:
        return jsonify({'error': 'Password must be at least 6 characters'}), 400
    conn = get_users_db()
    row  = conn.execute(
        'SELECT email FROM users WHERE reset_token=? AND reset_expires>?',
        (token, datetime.utcnow().isoformat())
    ).fetchone()
    if not row:
        conn.close()
        return jsonify({'error': 'Invalid or expired reset link'}), 400
    conn.execute(
        'UPDATE users SET password_hash=?, reset_token=NULL, reset_expires=NULL WHERE email=?',
        (generate_password_hash(password, method='pbkdf2:sha256'), row['email'])
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True})


@app.route('/')
def index():
    return app.send_static_file('index.html')

@app.route('/send-sms', methods=['POST'])
def send_sms():
    to   = request.json.get('to')
    body = request.json.get('body', 'test from EZ')
    r = requests.post(
        f'https://api.twilio.com/2010-04-01/Accounts/{TWILIO_SID}/Messages.json',
        auth=HTTPBasicAuth(TWILIO_SID, TWILIO_TOKEN),
        data={'To': to, 'From': TWILIO_FROM, 'Body': body}
    )
    return jsonify(r.json()), r.status_code

@app.route('/sp500')
def sp500_data():
    start = request.args.get('start', '')
    r = requests.get(
        'https://api.stlouisfed.org/fred/series/observations',
        params={
            'series_id': 'SP500',
            'api_key': FRED_API_KEY,
            'file_type': 'json',
            'sort_order': 'asc',
            'observation_start': start,
        }
    )
    return jsonify(r.json()), r.status_code

@app.route('/sp500/forecast')
def sp500_forecast():
    p     = max(0, min(int(request.args.get('p',     5)), 10))
    d     = max(0, min(int(request.args.get('d',     1)),  2))
    q     = max(0, min(int(request.args.get('q',     0)),  5))
    steps = max(5, min(int(request.args.get('steps', 30)), 90))

    start = (datetime.now() - timedelta(days=730)).strftime('%Y-%m-%d')
    r = requests.get('https://api.stlouisfed.org/fred/series/observations', params={
        'series_id': 'SP500', 'api_key': FRED_API_KEY,
        'file_type': 'json', 'sort_order': 'asc', 'observation_start': start
    })
    obs    = [o for o in r.json()['observations'] if o['value'] != '.']
    values = [float(o['value']) for o in obs]

    model  = ARIMA(values, order=(p, d, q))
    result = model.fit()
    fc     = result.get_forecast(steps=steps)
    preds  = fc.predicted_mean.tolist()
    ci     = fc.conf_int().tolist()

    last_date    = datetime.strptime(obs[-1]['date'], '%Y-%m-%d')
    future_dates = []
    dd = last_date
    while len(future_dates) < steps:
        dd += timedelta(days=1)
        if dd.weekday() < 5:
            future_dates.append(dd.strftime('%Y-%m-%d'))

    return jsonify({
        'params':      {'p': p, 'd': d, 'q': q, 'steps': steps},
        'last_actual': {'date': obs[-1]['date'], 'value': values[-1]},
        'recent':      [{'date': o['date'], 'value': float(o['value'])} for o in obs[-60:]],
        'forecast':    [{'date': future_dates[i], 'value': round(preds[i], 2),
                         'lower': round(ci[i][0], 2), 'upper': round(ci[i][1], 2)}
                        for i in range(steps)]
    })

def fetch_emails_imap(account, limit=10):
    try:
        mail = imaplib.IMAP4_SSL(account['host'], 993)
        mail.login(account['user'], account['pass'])
        mail.select('INBOX')
        _, data = mail.search(None, 'ALL')
        ids = data[0].split()
        recent_ids = ids[-limit:] if len(ids) > limit else ids
        results = []
        for uid in reversed(recent_ids):
            _, msg_data = mail.fetch(uid, '(RFC822)')
            msg = email_lib.message_from_bytes(msg_data[0][1])
            raw_subj = msg.get('Subject', '')
            subj_parts = decode_email_header(raw_subj)
            subject = ''.join(
                part.decode(enc or 'utf-8') if isinstance(part, bytes) else part
                for part, enc in subj_parts
            )
            body = ''
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == 'text/plain':
                        body = part.get_payload(decode=True).decode('utf-8', errors='replace')[:500]
                        break
            else:
                body = msg.get_payload(decode=True).decode('utf-8', errors='replace')[:500]
            results.append({
                'from': msg.get('From', ''),
                'subject': subject,
                'date': msg.get('Date', ''),
                'body': body,
            })
        mail.logout()
        return results
    except Exception as e:
        return [{'error': str(e)}]


@app.route('/email/fetch')
def email_fetch():
    if not EMAIL_ACCOUNTS:
        return jsonify({'error': 'No email accounts configured in .env'}), 503
    all_emails = {}
    for acct in EMAIL_ACCOUNTS:
        all_emails[acct['name']] = fetch_emails_imap(acct, limit=10)
    return jsonify(all_emails)


def fetch_headlines(url, limit=6):
    feed = feedparser.parse(url)
    items = []
    for entry in feed.entries[:limit]:
        title = (entry.get('title') or '').strip()
        summary = (entry.get('summary') or entry.get('description') or '').strip()
        summary = re.sub(r'<[^>]+>', '', summary)[:300]
        if title:
            items.append(f"- {title}: {summary}" if summary else f"- {title}")
    return items


@app.route('/news/brief')
@app.route('/ask', methods=['POST'])
def ask():
    query = (request.json or {}).get('query', '').strip()
    if not query:
        return jsonify({'error': 'No query provided.'}), 400
    if not ANTHROPIC_API_KEY:
        return jsonify({'error': 'ANTHROPIC_API_KEY not set.'}), 503

    def generate():
        try:
            client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
            content = query
            if EMAIL_ACCOUNTS and any(kw in query.lower() for kw in EMAIL_KEYWORDS):
                email_ctx_lines = []
                for acct in EMAIL_ACCOUNTS:
                    msgs = fetch_emails_imap(acct, limit=10)
                    email_ctx_lines.append(f"\n--- {acct['name']} (recent 10 emails) ---")
                    for m in msgs:
                        if 'error' in m:
                            email_ctx_lines.append(f"  [Could not fetch {acct['name']}: {m['error']}]")
                        else:
                            email_ctx_lines.append(
                                f"  From: {m['from']}\n  Date: {m['date']}\n"
                                f"  Subject: {m['subject']}\n  Body: {m['body'][:200]}\n"
                            )
                context_block = "\n".join(email_ctx_lines)
                content = f"Here are the user's recent emails:\n{context_block}\n\nUser question: {query}"
            messages = [{'role': 'user', 'content': content}]
            # agentic loop: Claude may call web_search one or more times
            while True:
                response = client.messages.create(
                    model='claude-haiku-4-5-20251001',
                    max_tokens=1024,
                    tools=[{'type': 'web_search_20250305', 'name': 'web_search'}],
                    messages=messages
                )
                # yield any text blocks immediately
                for block in response.content:
                    if block.type == 'text':
                        yield block.text
                # stop if Claude is done
                if response.stop_reason == 'end_turn':
                    break
                # otherwise Claude used a tool — add its turn and loop
                messages.append({'role': 'assistant', 'content': response.content})
                messages.append({
                    'role': 'user',
                    'content': [
                        {'type': 'tool_result', 'tool_use_id': b.id, 'content': ''}
                        for b in response.content if b.type == 'tool_use'
                    ]
                })
        except Exception as e:
            yield f'\n\n[Error: {e}]'

    return Response(stream_with_context(generate()), mimetype='text/plain')


@app.route('/news/brief')
def news_brief():
    global _news_cache
    if _news_cache['brief'] and time.time() - _news_cache['ts'] < NEWS_CACHE_TTL:
        cached = dict(_news_cache)
        cached['cached'] = True
        return jsonify(cached)

    if not ANTHROPIC_API_KEY:
        return jsonify({'error': 'ANTHROPIC_API_KEY not set. Run: export ANTHROPIC_API_KEY=sk-ant-...'}), 503

    all_lines = []
    source_status = []
    for name, url in FEEDS:
        try:
            items = fetch_headlines(url)
            if items:
                all_lines.append(f"\n{name}:\n" + "\n".join(items))
                source_status.append({'name': name, 'ok': True, 'count': len(items)})
            else:
                source_status.append({'name': name, 'ok': False, 'count': 0})
        except Exception:
            source_status.append({'name': name, 'ok': False, 'count': 0})

    if not all_lines:
        return jsonify({'error': 'Could not fetch any news feeds.'}), 502

    prompt = (
        "You are a news editor. Based on the following headlines from Reuters, TechCrunch, "
        "and Bloomberg, write exactly 3 bullet points covering the 3 most important stories. "
        "Each bullet must be under 60 words. Total response must be under 200 words. "
        "Format each bullet starting with a bold headline in ALL CAPS followed by a colon, "
        "then one or two sentences. Use this exact format:\n"
        "• HEADLINE: explanation.\n"
        "• HEADLINE: explanation.\n"
        "• HEADLINE: explanation.\n\n"
        + "\n".join(all_lines)
    )

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model='claude-opus-4-6',
            max_tokens=600,
            messages=[{'role': 'user', 'content': prompt}]
        )
        brief = message.content[0].text.strip()
    except Exception as e:
        return jsonify({'error': str(e)}), 502

    _news_cache.update({'brief': brief, 'sources': source_status, 'ts': time.time(), 'cached': False})
    return jsonify(_news_cache)


def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    return conn


def rows_to_contacts(rows):
    contacts = []
    for row in rows:
        phones = [p for p in (row['phones'] or '').split(',') if p]
        emails = [e for e in (row['emails'] or '').split(',') if e]
        contacts.append({
            'id': row['id'], 'fn': row['fn'], 'org': row['org'],
            'phones': phones, 'emails': emails
        })
    return contacts


@app.route('/db/contacts')
def db_contacts():
    if not os.path.exists(DB_PATH):
        return jsonify({'error': 'Database not found. Run build_db.py first.'}), 503
    conn = get_db()
    rows = conn.execute('''
        SELECT c.id, c.fn, c.org,
               GROUP_CONCAT(DISTINCT p.number) AS phones,
               GROUP_CONCAT(DISTINCT e.address) AS emails
        FROM contacts c
        LEFT JOIN phones p ON p.contact_id = c.id
        LEFT JOIN emails e ON e.contact_id = c.id
        GROUP BY c.id
        ORDER BY c.fn COLLATE NOCASE
    ''').fetchall()
    conn.close()
    contacts = rows_to_contacts(rows)
    return jsonify({'contacts': contacts, 'count': len(contacts)})


@app.route('/db/search')
def db_search():
    if not os.path.exists(DB_PATH):
        return jsonify({'error': 'Database not found. Run build_db.py first.'}), 503
    q = request.args.get('q', '').strip()
    conn = get_db()
    if not q:
        rows = conn.execute('''
            SELECT c.id, c.fn, c.org,
                   GROUP_CONCAT(DISTINCT p.number) AS phones,
                   GROUP_CONCAT(DISTINCT e.address) AS emails
            FROM contacts c
            LEFT JOIN phones p ON p.contact_id = c.id
            LEFT JOIN emails e ON e.contact_id = c.id
            GROUP BY c.id ORDER BY c.fn COLLATE NOCASE
        ''').fetchall()
    else:
        rows = conn.execute('''
            SELECT DISTINCT c.id, c.fn, c.org,
                   GROUP_CONCAT(DISTINCT p.number) AS phones,
                   GROUP_CONCAT(DISTINCT e.address) AS emails
            FROM contacts c
            LEFT JOIN phones p ON p.contact_id = c.id
            LEFT JOIN emails e ON e.contact_id = c.id
            WHERE c.fn      LIKE '%'||?||'%' COLLATE NOCASE
               OR c.org     LIKE '%'||?||'%' COLLATE NOCASE
               OR p.number  LIKE '%'||?||'%'
               OR e.address LIKE '%'||?||'%' COLLATE NOCASE
            GROUP BY c.id ORDER BY c.fn COLLATE NOCASE
        ''', (q, q, q, q)).fetchall()
    conn.close()
    contacts = rows_to_contacts(rows)
    return jsonify({'contacts': contacts, 'count': len(contacts)})


@app.route('/db/query', methods=['POST'])
def db_query():
    if not os.path.exists(DB_PATH):
        return jsonify({'error': 'Database not found. Run build_db.py first.'}), 503
    sql = (request.json or {}).get('sql', '').strip()
    if not sql:
        return jsonify({'error': 'No SQL provided.'}), 400

    # strip single-line comments and get first token
    stripped = re.sub(r'--[^\n]*', '', sql).strip()
    first_token = stripped.split()[0].upper() if stripped.split() else ''
    if first_token != 'SELECT':
        return jsonify({'error': 'Only SELECT queries are allowed.'}), 403

    # block dangerous keywords
    upper_sql = sql.upper()
    for blocked in ('ATTACH', 'PRAGMA', 'INSERT', 'UPDATE', 'DELETE', 'DROP', 'CREATE', 'ALTER'):
        if re.search(r'\b' + blocked + r'\b', upper_sql):
            return jsonify({'error': f'Keyword {blocked} is not allowed.'}), 403

    # cap results if no LIMIT clause
    if not re.search(r'\bLIMIT\b', upper_sql):
        sql = sql.rstrip(';') + ' LIMIT 1000'

    try:
        conn = get_db()
        cur = conn.execute(sql)
        columns = [d[0] for d in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchall()]
        conn.close()
        return jsonify({'columns': columns, 'rows': rows, 'count': len(rows)})
    except sqlite3.Error as e:
        return jsonify({'error': str(e)}), 400


@app.route('/products/data')
def products_data():
    if os.path.exists(PRODUCTS_FILE):
        with open(PRODUCTS_FILE) as f:
            return jsonify(json.load(f))
    return jsonify([
        {'image': None, 'description': ''},
        {'image': None, 'description': ''},
    ])


@app.route('/products/upload', methods=['POST'])
def products_upload():
    slot = request.form.get('slot', '0')
    f    = request.files.get('file')
    if not f or not f.filename:
        return jsonify({'error': 'No file'}), 400
    ext = f.filename.rsplit('.', 1)[-1].lower()
    if ext not in ALLOWED_EXTS:
        return jsonify({'error': 'File type not allowed'}), 400
    filename = secure_filename(f'product_{slot}.{ext}')
    f.save(os.path.join(UPLOADS_DIR, filename))
    return jsonify({'url': f'/uploads/{filename}'})


@app.route('/products/save', methods=['POST'])
def products_save():
    data = request.json or []
    with open(PRODUCTS_FILE, 'w') as f:
        json.dump(data, f)
    return jsonify({'ok': True})


@app.route('/uploads/<path:filename>')
def serve_upload(filename):
    return send_from_directory(UPLOADS_DIR, filename)


# ── Payment routes ──

@app.route('/pay/status')
def pay_status():
    if 'user' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    product_id = int(request.args.get('product', 0))
    conn = get_users_db()
    row  = conn.execute(
        'SELECT id FROM payments WHERE email=? AND product_id=?',
        (session['user'], product_id)
    ).fetchone()
    conn.close()
    return jsonify({'paid': row is not None})


@app.route('/pay/create-session', methods=['POST'])
def pay_create_session():
    if 'user' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    if not stripe.api_key:
        return jsonify({'error': 'Stripe not configured'}), 503
    product_id = (request.json or {}).get('product_id', 0)
    try:
        checkout = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {'name': f'Comment unlock — Product {product_id + 1}'},
                    'unit_amount': 100,  # $1.00
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=f'http://localhost:5001/products.html?paid=1&product={product_id}&session_id={{CHECKOUT_SESSION_ID}}',
            cancel_url='http://localhost:5001/products.html',
        )
        return jsonify({'url': checkout.url})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/pay/verify', methods=['POST'])
def pay_verify():
    if 'user' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    data       = request.json or {}
    session_id = data.get('session_id', '')
    product_id = int(data.get('product_id', 0))
    try:
        checkout = stripe.checkout.Session.retrieve(session_id)
        if checkout.payment_status != 'paid':
            return jsonify({'error': 'Payment not completed'}), 402
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    conn = get_users_db()
    try:
        conn.execute(
            'INSERT OR IGNORE INTO payments (email, product_id, session_id, paid_at) VALUES (?,?,?,?)',
            (session['user'], product_id, session_id, datetime.utcnow().isoformat())
        )
        conn.commit()
    except Exception:
        pass
    conn.close()
    return jsonify({'ok': True})


# ── Credit routes ──

@app.route('/credits/balance')
def credits_balance():
    if 'user' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    conn = get_users_db()
    row = conn.execute('SELECT credit_balance FROM users WHERE email=?', (session['user'],)).fetchone()
    conn.close()
    return jsonify({'balance': row['credit_balance'] if row else 0})


@app.route('/credits/buy-session', methods=['POST'])
def credits_buy_session():
    if 'user' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    if not stripe.api_key:
        return jsonify({'error': 'Stripe not configured'}), 503
    amount_dollars = int((request.json or {}).get('amount', 5))
    if amount_dollars < 5 or amount_dollars % 5 != 0:
        return jsonify({'error': 'Amount must be a multiple of $5 (min $5)'}), 400
    try:
        checkout = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price_data': {
                    'currency': 'usd',
                    'product_data': {'name': f'${amount_dollars} Comment Credits ({amount_dollars} comments)'},
                    'unit_amount': amount_dollars * 100,
                },
                'quantity': 1,
            }],
            mode='payment',
            success_url=f'http://localhost:5001/products.html?credits_paid=1&amount={amount_dollars}&session_id={{CHECKOUT_SESSION_ID}}',
            cancel_url='http://localhost:5001/products.html',
        )
        return jsonify({'url': checkout.url})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/credits/verify', methods=['POST'])
def credits_verify():
    if 'user' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    data = request.json or {}
    session_id = data.get('session_id', '')
    amount_dollars = int(data.get('amount', 5))
    try:
        checkout = stripe.checkout.Session.retrieve(session_id)
        if checkout.payment_status != 'paid':
            return jsonify({'error': 'Payment not completed'}), 402
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    conn = get_users_db()
    conn.execute(
        'UPDATE users SET credit_balance = credit_balance + ? WHERE email=?',
        (amount_dollars * 100, session['user'])
    )
    conn.commit()
    conn.close()
    return jsonify({'ok': True, 'added': amount_dollars * 100})


# ── Comment routes ──

@app.route('/comments')
def get_comments():
    product_id = int(request.args.get('product', 0))
    conn = get_users_db()
    rows = conn.execute(
        'SELECT email, body, created_at FROM comments WHERE product_id=? ORDER BY created_at DESC',
        (product_id,)
    ).fetchall()
    conn.close()
    return jsonify([{'email': r['email'], 'body': r['body'], 'created_at': r['created_at']} for r in rows])


@app.route('/comments', methods=['POST'])
def post_comment():
    if 'user' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    data       = request.json or {}
    product_id = int(data.get('product_id', 0))
    body       = data.get('body', '').strip()
    if not body:
        return jsonify({'error': 'Comment cannot be empty'}), 400
    conn = get_users_db()
    # Atomically deduct 100 cents; only proceeds if balance >= 100
    conn.execute(
        'UPDATE users SET credit_balance = credit_balance - 100 WHERE email=? AND credit_balance >= 100',
        (session['user'],)
    )
    if conn.total_changes == 0:
        conn.close()
        return jsonify({'error': 'Insufficient credits'}), 402
    conn.execute(
        'INSERT INTO comments (email, product_id, body, created_at) VALUES (?,?,?,?)',
        (session['user'], product_id, body, datetime.utcnow().isoformat())
    )
    conn.commit()
    new_balance = conn.execute(
        'SELECT credit_balance FROM users WHERE email=?', (session['user'],)
    ).fetchone()['credit_balance']
    conn.close()
    return jsonify({'ok': True, 'balance': new_balance})


IMAGE_EXTS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'tiff', 'tif', 'heic', 'heif'}
VIDEO_EXTS  = {'mp4', 'mov', 'avi', 'mkv', 'webm', 'm4v'}
HOME_DIR    = pathlib.Path.home()


def _safe_path(path_str, default=None):
    """Resolve path and ensure it stays within the user's home directory."""
    try:
        p = pathlib.Path(path_str).expanduser().resolve()
        p.relative_to(HOME_DIR)  # raises ValueError if outside home
        return p
    except (ValueError, Exception):
        return default


@app.route('/files/browse')
def files_browse():
    if 'user' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    conn = get_users_db()
    row = conn.execute('SELECT is_admin FROM users WHERE email=?', (session['user'],)).fetchone()
    conn.close()
    if not row or not row['is_admin']:
        return jsonify({'error': 'Admin only'}), 403

    path_param = request.args.get('path', str(HOME_DIR))
    target = _safe_path(path_param, HOME_DIR)
    if not target or not target.is_dir():
        target = HOME_DIR

    flat = request.args.get('flat', 'false').lower() == 'true'
    entries = []

    try:
        if flat:
            # Recursively collect all images/videos sorted by most recent first
            found = []
            for item in target.rglob('*'):
                if not item.is_file() or item.name.startswith('.'):
                    continue
                ext = item.suffix.lower().lstrip('.')
                kind = 'image' if ext in IMAGE_EXTS else ('video' if ext in VIDEO_EXTS else None)
                if kind:
                    try:
                        found.append((item.stat().st_mtime, item, kind))
                    except OSError:
                        pass
            found.sort(key=lambda x: -x[0])
            for mtime, item, kind in found:
                entries.append({'name': item.name, 'path': str(item), 'kind': kind, 'mtime': mtime})
        else:
            def _sort_key(p):
                try:
                    return (not p.is_dir(), -p.stat().st_mtime)
                except OSError:
                    return (not p.is_dir(), 0)
            items = sorted(target.iterdir(), key=_sort_key)
            for item in items:
                if item.name.startswith('.'):
                    continue
                ext = item.suffix.lower().lstrip('.')
                try:
                    mtime = item.stat().st_mtime
                except OSError:
                    mtime = 0
                if item.is_dir():
                    entries.append({'name': item.name, 'path': str(item), 'kind': 'dir', 'mtime': mtime})
                elif ext in IMAGE_EXTS:
                    entries.append({'name': item.name, 'path': str(item), 'kind': 'image', 'mtime': mtime})
                elif ext in VIDEO_EXTS:
                    entries.append({'name': item.name, 'path': str(item), 'kind': 'video', 'mtime': mtime})
    except PermissionError:
        pass

    parent = str(target.parent) if target != HOME_DIR else None
    return jsonify({'path': str(target), 'parent': parent, 'entries': entries, 'flat': flat})


@app.route('/files/pick', methods=['POST'])
def files_pick():
    if 'user' not in session:
        return jsonify({'error': 'Not logged in'}), 401
    conn = get_users_db()
    row = conn.execute('SELECT is_admin FROM users WHERE email=?', (session['user'],)).fetchone()
    conn.close()
    if not row or not row['is_admin']:
        return jsonify({'error': 'Admin only'}), 403

    data = request.json or {}
    slot = int(data.get('slot', 0))
    src  = _safe_path(data.get('path', ''))
    if not src or not src.is_file():
        return jsonify({'error': 'File not found or access denied'}), 404

    ext = src.suffix.lower().lstrip('.')
    if ext not in ALLOWED_EXTS:
        return jsonify({'error': 'File type not allowed'}), 400

    dest_name = secure_filename(f'product_{slot}{src.suffix.lower()}')
    shutil.copy2(src, os.path.join(UPLOADS_DIR, dest_name))
    return jsonify({'url': f'/uploads/{dest_name}'})


PREVIEW_CACHE_DIR = pathlib.Path(tempfile.gettempdir()) / 'hw_preview_cache'
PREVIEW_CACHE_DIR.mkdir(exist_ok=True)
NATIVE_BROWSER_EXTS = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp'}


@app.route('/files/preview')
def files_preview():
    if 'user' not in session:
        return ('', 401)
    src = _safe_path(request.args.get('path', ''))
    if not src or not src.is_file():
        return ('', 404)
    ext = src.suffix.lower().lstrip('.')
    if ext not in (IMAGE_EXTS | VIDEO_EXTS):
        return ('', 400)

    # Formats browsers render natively — serve directly
    if ext in NATIVE_BROWSER_EXTS:
        mime = mimetypes.guess_type(str(src))[0] or 'image/jpeg'
        return send_file(str(src), mimetype=mime)

    # All other formats (HEIC, TIFF, video…) — use macOS Quick Look
    cache_key = hashlib.md5(str(src).encode()).hexdigest()
    cached = PREVIEW_CACHE_DIR / f'{cache_key}.png'
    if not cached.exists():
        try:
            subprocess.run(
                ['qlmanage', '-t', '-s', '400', '-o', str(PREVIEW_CACHE_DIR), str(src)],
                capture_output=True, timeout=15
            )
            generated = PREVIEW_CACHE_DIR / f'{src.name}.png'
            if generated.exists():
                generated.rename(cached)
        except Exception:
            pass
    if cached.exists():
        return send_file(str(cached), mimetype='image/png')
    return ('', 404)


if __name__ == '__main__':
    print('Open http://localhost:5001 in your browser')
    app.run(port=5001)
