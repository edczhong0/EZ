# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

A full-stack web application built with Flask (Python backend) and vanilla HTML/CSS/JS (frontend). Originally a "hello world" static page, now a multi-feature platform with user auth, a credit-based comment system, product management, a server-side file browser, and an AI assistant.

## Stack

- **Backend**: Python 3 / Flask (`server.py`) — single file, all routes in one place
- **Database**: SQLite — two databases: `users.db` (users, credits, payments, comments) and `contacts.db` (address book)
- **Frontend**: Plain HTML/CSS/JS — no build tools, no framework, no npm
- **Auth**: Session-based via Flask sessions, passwords hashed with werkzeug
- **Payments**: Stripe Checkout for credit purchases
- **AI**: Anthropic Claude API (streaming, with web search tool)
- **SMS**: Twilio
- **File storage**: Local `uploads/` directory

## Running the project

```bash
pip3 install -r requirements.txt
python3 server.py
# Open http://localhost:5001
```

Secrets are loaded from `.env` (never committed). See `.env` for required keys.

## Key files

| File | Purpose |
|------|---------|
| `server.py` | All Flask routes — auth, credits, comments, products, file browser, AI, email, SMS |
| `products.html` | Product page — photo/video display, comments, credit system, admin file browser |
| `login.html` | Auth page — sign in, register, forgot password, product showcase panel |
| `index.html` | Home page — hello world animation, nav to other pages |
| `users.db` | SQLite — users, credit balances, payments, comments |
| `.env` | Secret keys — never committed to git |

## Architecture

### Backend (`server.py`)
- One Flask app, all routes in a single file
- `init_users_db()` runs on startup — creates tables and runs any `ALTER TABLE` migrations
- `get_users_db()` / `get_db()` — open SQLite connections per request
- Static files (HTML, uploads) served directly by Flask

### Credit system
- New users get 500 cents ($5) on registration
- Each comment costs 100 cents ($1), deducted atomically with `WHERE credit_balance >= 100`
- Users buy more credits via Stripe Checkout in $5/$10/$15/$20 increments
- Balance returned in every `POST /comments` response so UI updates instantly

### File browser (`/files/browse`, `/files/pick`, `/files/preview`)
- Admin-only server-side file browser — bypasses macOS browser sandbox
- Restricted to `$HOME` directory via `_safe_path()`
- `flat=true` mode: recursively collects all images/videos sorted by mtime desc (used for Photos Library)
- Thumbnails generated via macOS `qlmanage` (Quick Look) — supports HEIC, video, all native formats
- Thumbnail cache stored in system temp dir

### Database schema (users.db)
- `users` — id, email, password_hash, created_at, reset_token, reset_expires, is_admin, credit_balance
- `payments` — id, email, product_id, session_id, paid_at (legacy Stripe per-product payments)
- `comments` — id, email, product_id, body, created_at

## Development notes

- No build step — edit HTML/JS/CSS files and refresh browser
- Server must be restarted (`python3 server.py`) after any `server.py` change
- Browser hard-refresh (`Cmd+Shift+R`) needed after `products.html` or `login.html` changes
- First user to register becomes admin automatically
- Admin can upload product photos/videos and edit descriptions
- `.env` is gitignored — credentials stay local only
