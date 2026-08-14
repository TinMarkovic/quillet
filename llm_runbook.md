# QUILLET LLM Runbook

This document is written for an LLM agent. Follow it exactly. It tells you how to integrate
QUILLET into an existing Flask app, or stand it up as a Docker service.

---

## What QUILLET is

QUILLET is a Python package that adds newsletter + microblog functionality to a Flask application.
It handles:

- A web archive of posts (Markdown rendered to HTML)
- Email subscriptions with double opt-in
- Sending posts to confirmed subscribers via email
- A minimal browser admin UI (Basic Auth protected)
- A JSON API for all admin operations

The package is at `/home/tin/projects/quillet/` in this workspace (if you are working in that
repo) or installable via pip.

---

## Package layout

```
quillet/
├── __init__.py           → exposes create_blueprint, create_app, create_asgi_app
├── factory.py            → wires everything together (create_asgi_app = REST + MCP)
├── robots.py             → robots_txt() / register_robots() for site-root /robots.txt
├── models.py             → Newsletter, Post, Subscriber, Page (NamedTuples)
├── routes.py             → public + JSON API route handlers
├── api_ops.py            → in-process admin ops shared by JSON API and MCP
├── mcp_server.py         → Streamable HTTP MCP (optional extra)
├── mcp.md                → agent bootstrap served at GET /{slug}/api/mcp.md
├── openapi.yaml          → OpenAPI 3 spec served at GET /{slug}/api/openapi.yaml
├── admin.py              → browser admin UI route handlers
├── auth.py               → HTTP Basic Auth decorator
├── cli.py                → flask quillet ... CLI commands
├── db/
│   ├── __init__.py       → NewsletterRepository Protocol
│   ├── sqlalchemy.py     → SQLAlchemy implementation (SQLite / Postgres)
│   └── supabase_rest.py  → Supabase REST API implementation
├── email/
│   ├── __init__.py       → EmailSender Protocol
│   ├── mailgun.py        → Mailgun HTTP API
│   ├── smtp.py           → stdlib smtplib
│   └── noop.py           → silent sender for dev/testing
└── templates/quillet/
    ├── base.html
    ├── post_list.html
    ├── post_detail.html
    ├── subscribe_confirm.html
    ├── unsubscribe_confirm.html
    └── admin/
        ├── dashboard.html
        ├── post_form.html
        └── subscribers.html
```

---

## Data models

All models are `NamedTuple` — immutable, no ORM magic.

```python
class Newsletter(NamedTuple):
    id: int
    slug: str           # URL slug, e.g. "my-blog" — must be unique
    name: str           # Display name
    from_email: str     # Sender address
    from_name: str      # Sender display name
    reply_to: str | None

class Post(NamedTuple):
    id: int
    newsletter_id: int
    slug: str           # URL slug — unique per newsletter
    title: str
    body_md: str        # Raw Markdown source
    published_at: datetime | None   # None = draft
    sent_at: datetime | None        # None = not yet emailed

class Subscriber(NamedTuple):
    id: int
    newsletter_id: int
    email: str
    token: str          # Used in confirm + unsubscribe URLs
    confirmed_at: datetime | None   # None = pending confirmation
    unsubscribed: bool = False
    created_at: datetime | None = None
```

---

## Mode 1: Adding to an existing Flask app

### Step 1 — Install

```bash
pip install "quillet[sqlalchemy,mailgun]"
# or for SMTP:
pip install "quillet[sqlalchemy,smtp]"
# or for Supabase REST:
pip install "quillet[supabase]"
```

### Step 2 — Register the blueprint

Add this to wherever you create your Flask app (e.g. `main.py`, `app.py`, `__init__.py`):

```python
from quillet import create_blueprint, register_robots, robots_txt
from quillet.db.sqlalchemy import SQLAlchemyRepository
from quillet.email.mailgun import MailgunSender

db = SQLAlchemyRepository("sqlite:///newsletter.db")
app.register_blueprint(
    create_blueprint(
        db=db,
        email=MailgunSender(api_key=os.environ["MAILGUN_API_KEY"], domain=os.environ["MAILGUN_DOMAIN"]),
        admin_password=os.environ["QUILLET_ADMIN_PASSWORD"],
    ),
    url_prefix="/newsletter",
)
# Do not auto-register /robots.txt from the blueprint — the host may already own it.
register_robots(app, db, url_prefix="/newsletter", base_url="https://example.com")
# Or splice: return extra + robots_txt(db, url_prefix="/newsletter", base_url="https://example.com")
```

The `url_prefix` is arbitrary — you choose where routes live. With `/newsletter`:
- Archive: `/newsletter/<slug>/`
- Admin: `/newsletter/<slug>/admin/`

### Step 3 — Create a newsletter

Run once. FLASK_APP must point to your app.

```bash
export FLASK_APP=your_module:app
flask quillet create "My Blog" --slug=blog --from-email=hi@example.com --from-name="My Blog"
```

That's it. Visiting `/newsletter/blog/` should return HTTP 200.

### Step 4 — Verify

```python
# Paste this into a Python shell to check the DB is seeded
from quillet.db.sqlalchemy import SQLAlchemyRepository
db = SQLAlchemyRepository("sqlite:///newsletter.db")
print(db.get_newsletter("blog"))  # should print a Newsletter(...)
```

---

## Mode 2: Standalone Docker service

### Step 1 — docker-compose.yml

```yaml
services:
  quillet:
    image: ghcr.io/tinthe/quillet:latest   # or build: .
    ports:
      - "8000:8000"
    volumes:
      - quillet_data:/data
    environment:
      QUILLET_MODE: web
      QUILLET_ADMIN_PASSWORD: changeme      # CHANGE THIS
      QUILLET_BASE_URL: https://your-domain.com
      QUILLET_DB_BACKEND: sqlalchemy
      QUILLET_DB_URL: sqlite:////data/quillet.db
      QUILLET_EMAIL_BACKEND: mailgun
      QUILLET_MAILGUN_API_KEY: key-...
      QUILLET_MAILGUN_DOMAIN: mg.example.com

volumes:
  quillet_data:
```

### Step 2 — Start and create a newsletter

```bash
docker compose up -d
docker compose exec quillet flask quillet create "My Blog" \
  --slug=blog \
  --from-email=hi@example.com \
  --from-name="My Blog"
```

### Step 3 — nginx proxy example

```nginx
location /newsletter/ {
    proxy_pass http://127.0.0.1:8000/;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
}
```

---

## Mode 3: Headless API (JSON only)

Set `QUILLET_MODE=api`. All endpoints return JSON. Admin UI is automatically disabled.

---

## All routes reference

Replace `<slug>` with your newsletter slug (e.g. `blog`).

### Public (no auth)

```
GET  /<slug>/
     Returns post archive.
     HTML by default; JSON if Accept: application/json; markdown if Accept: text/markdown.
     Query: cursor (opaque), limit (default 20, max 100), partial=1 (HTML fragment).
     Response: { newsletter: {...}, posts: [...], next_cursor: ... }

GET  /<slug>/posts/<post_slug>
     Returns single post. Only works for published posts (published_at not null).
     HTML by default; JSON if Accept: application/json; markdown if Accept: text/markdown.
     Response: { newsletter: {...}, post: {...} }

POST /<slug>/subscribe
     Subscribe. Sends confirmation email to the address.
     Body: form-encoded email=... OR JSON {"email": "..."}
     Response: HTML confirmation page OR {"ok": true, "message": "Confirmation email sent."} (201)

GET  /<slug>/confirm/<token>
     Confirm subscription. Token comes from the confirmation email.
     Pending unconfirmed rows expire after 3 days (hard-deleted).
     Response: HTML state page OR {"ok": true} / {"error": "..."} (404)

GET  /<slug>/unsubscribe/<token>
     Confirm form. Does not unsubscribe. JSON GET → 405 Allow: POST.

POST /<slug>/unsubscribe/<token>
     Unsubscribe. Token is embedded in every sent email.
     Response: HTML state page OR {"ok": true}

GET  /<slug>/feed.xml
     RSS 2.0 of the latest 50 published posts. No cursor.

GET  /<slug>/sitemap.xml
     URL index of the archive and all published posts.

GET  /<slug>/llms.txt
     LLM discovery index of published posts.
```

### JSON API (Basic Auth: username=admin, password=QUILLET_ADMIN_PASSWORD)

```
GET  /<slug>/api/openapi.yaml
     OpenAPI 3 spec for the curated admin JSON API. Public (no auth).
     Response: YAML document (application/yaml)

GET  /<slug>/api/mcp.md
     Agent bootstrap for MCP: how to write .cursor/mcp.json with a URL to
     this instance's Streamable HTTP MCP (/{slug}/mcp/). Public (no auth).
     Password is not embedded — the human supplies it to the agent.
     Response: Markdown (text/markdown)

MCP  /<slug>/mcp/   (ASGI / uvicorn only)
     MCP Streamable HTTP. Same Basic Auth as the JSON API.
     Tools run in-process against the same DB and email backends as REST.

GET  /<slug>/api/posts
     List posts including drafts. Optional query: status=all|draft|published|unsent|sent,
     cursor, limit (default 20, max 100).
     Response: {"posts": [{id, newsletter_id, slug, title, body_md, published_at, sent_at}, ...], "next_cursor": ...}

GET  /<slug>/api/posts/<post_slug>
     Fetch a single post by slug, including drafts.
     Response: {"post": {...}} (200)

POST /<slug>/api/posts
     Create a draft post.
     Body: {"title": "...", "slug": "...", "body_md": "..."}
     Response: {"post": {...}} (201)

PUT  /<slug>/api/posts/<post_slug>
     Update title/body. slug is optional — omit to keep the existing slug.
     Body: {"title": "...", "body_md": "...", "slug": "...?"}
     Response: {"post": {...}} (200)

POST /<slug>/api/posts/<post_slug>/publish
     Publish a draft post (sets published_at to now).
     Response: {"post": {...}} (200)

POST /<slug>/api/posts/<post_slug>/send-test
     Email a single preview address. Does NOT set sent_at.
     Body: {"email": "owner@example.com"}
     Response: {"ok": true} (200)

POST /<slug>/api/posts/<post_slug>/send
     Email the post to all confirmed subscribers.
     Post must be published first. Returns 400 if not published, 409 if already sent.
     Response: {"ok": true, "recipients": N} (200)

GET  /<slug>/api/subscribers
     List subscribers (confirmed and pending), a page at a time.
     Query: cursor, limit (default 20, max 100).
     Response: {"subscribers": [{id, newsletter_id, email, token, confirmed_at, created_at}, ...], "next_cursor": ...}
```

### Admin UI (Basic Auth, web mode only)

```
GET  /<slug>/admin/
     Dashboard. Post list with Draft/Published/Sent badges. Subscriber counts.

GET  /<slug>/admin/posts/new
POST /<slug>/admin/posts/new
     Form to create a new post. POST creates it and redirects to edit view.
     Form fields: title, slug (optional — auto-generated from title), body_md

GET  /<slug>/admin/posts/<post_slug>/edit
POST /<slug>/admin/posts/<post_slug>/edit
     Edit post form. Publish and Send buttons appear inline based on post state.

POST /<slug>/admin/posts/<post_slug>/publish
     Publish and redirect to dashboard.

POST /<slug>/admin/posts/<post_slug>/send
     Send and redirect to dashboard. No-op if already sent.

GET  /<slug>/admin/subscribers
     Table of all subscribers with confirmation status.
```

---

## Creating and publishing a post via the API

This is the full workflow in curl:

```bash
BASE=http://localhost:8000
SLUG=blog
AUTH="admin:your_password"

# Create draft
curl -s -u "$AUTH" -X POST "$BASE/$SLUG/api/posts" \
  -H "Content-Type: application/json" \
  -d '{"title":"Hello World","slug":"hello-world","body_md":"# Hello\n\nFirst post."}'

# Publish
curl -s -u "$AUTH" -X POST "$BASE/$SLUG/api/posts/hello-world/publish"

# Send to subscribers
curl -s -u "$AUTH" -X POST "$BASE/$SLUG/api/posts/hello-world/send"
```

---

## Environment variables — complete reference

| Variable | Required | Default | Notes |
|---|---|---|---|
| `QUILLET_MODE` | No | `web` | `web` or `api` |
| `QUILLET_ADMIN_PASSWORD` | Yes | — | Basic Auth password; username is always `admin` |
| `QUILLET_ADMIN_UI` | No | `true` | `false` disables browser admin; auto-false in `api` mode |
| `QUILLET_BASE_URL` | No | request host | Set in production for correct email links |
| `QUILLET_DB_BACKEND` | No | `sqlalchemy` | `sqlalchemy` or `supabase_rest` |
| `QUILLET_DB_URL` | No | `sqlite:////data/quillet.db` | Any SQLAlchemy URL |
| `QUILLET_SUPABASE_URL` | If supabase_rest | — | |
| `QUILLET_SUPABASE_KEY` | If supabase_rest | — | Anon key |
| `QUILLET_EMAIL_BACKEND` | No | `smtp` | `mailgun`, `smtp`, or `noop` |
| `QUILLET_MAILGUN_API_KEY` | If mailgun | — | |
| `QUILLET_MAILGUN_DOMAIN` | If mailgun | — | |
| `QUILLET_SMTP_FROM_EMAIL` | If smtp | — | |
| `QUILLET_SMTP_FROM_NAME` | No | — | |
| `QUILLET_SMTP_HOST` | No | `localhost` | |
| `QUILLET_SMTP_PORT` | No | `587` | |
| `QUILLET_SMTP_USE_TLS` | No | `true` | Set `false` for Mailhog / local SMTP |
| `QUILLET_SMTP_USERNAME` | No | — | |
| `QUILLET_SMTP_PASSWORD` | No | — | |
| `QUILLET_MCP_SLUGS` | No | *(auto from DB)* | Comma-separated newsletter slugs to mount at `/{slug}/mcp/` (SQLAlchemy auto-discovers; set this for other backends) |

---

## Writing a custom DB backend

Implement every method in this interface. No base class needed — Python structural typing (Protocol).

```python
class MyRepository:
    def create_newsletter(self, slug: str, name: str, from_email: str, from_name: str, reply_to: str | None) -> Newsletter: ...
    def get_newsletter(self, slug: str) -> Newsletter | None: ...

    def list_newsletter_slugs(self) -> list[str]: ...

    def list_posts(
        self,
        newsletter_slug: str,
        published_only: bool = True,
        *,
        limit: int = 20,
        before: str | None = None,
        status: str | None = None,
    ) -> Page: ...
    # Keyset page, newest first. Opaque `before` cursor. published_only uses (published_at, id);
    # all-status (admin) uses (id). Default page 20, max 100.

    def get_post(self, newsletter_slug: str, post_slug: str) -> Post | None: ...
    def create_post(self, newsletter_slug: str, title: str, slug: str, body_md: str) -> Post: ...
    def update_post(self, post_id: int, title: str, slug: str, body_md: str) -> Post: ...
    def publish_post(self, post_id: int) -> Post: ...
    # Sets published_at to datetime.now(timezone.utc). Returns updated Post.

    def mark_sent(self, post_id: int) -> None: ...
    # Sets sent_at to datetime.now(timezone.utc).

    def add_subscriber(self, newsletter_slug: str, email: str, token: str) -> Subscriber: ...
    # token is a pre-generated secrets.token_urlsafe(32) string.
    # confirmed_at should be None initially. Set created_at. Call purge_expired_pending first.

    def confirm_subscriber(self, token: str) -> Subscriber | None: ...
    # Sets confirmed_at. Returns None if token not found or pending row is older than 3 days
    # (delete the expired row).

    def get_subscriber_by_token(self, token: str) -> Subscriber | None: ...

    def list_confirmed_subscribers(self, newsletter_slug: str) -> list[Subscriber]: ...
    # Full set. Only confirmed (confirmed_at not None) and not unsubscribed. Used by send.

    def list_all_subscribers(
        self, newsletter_slug: str, *, limit: int = 20, before: str | None = None
    ) -> Page: ...
    # Keyset page on (created_at, id). Call purge_expired_pending first.

    def purge_expired_pending(self, newsletter_id: int) -> int: ...
    # Hard-delete pending (confirmed_at is None) older than 3 days.

    def unsubscribe(self, token: str) -> None: ...
    # Mark as unsubscribed. Should not raise if token not found.
```

---

## Writing a custom email backend

```python
class MyEmailSender:
    def send_confirmation(
        self,
        newsletter: Newsletter,
        subscriber: Subscriber,
        confirm_url: str,           # full URL to click — embed in email body
    ) -> None: ...

    def send_post(
        self,
        newsletter: Newsletter,
        post: Post,
        subscribers: list[Subscriber],
        unsubscribe_url_template: str,  # e.g. "https://example.com/blog/unsubscribe/{token}"
                                        # replace {token} with each subscriber's token
    ) -> None: ...
```

---

## Template overrides

All templates extend `quillet/base.html`. Override individual templates by placing files in the
same relative path where Flask looks first.

**Blueprint mode**: put overrides in your app's `templates/` folder:

```
your_app/templates/quillet/post_list.html      ← overrides the built-in
your_app/templates/quillet/base.html           ← overrides base layout
```

**Docker mode**: mount a volume to `/app/templates/quillet/`.

### Variables available in each template

`post_list.html`:
- `newsletter` — `Newsletter` NamedTuple
- `posts` — current page of published `Post` NamedTuples
- `next_cursor` — opaque string or None
- `subscribe_position` — `"start"` or `"end"`
- `description_html` — rendered archive description or `""`
- `subscribe_error` — string or None (set when subscribe form submission fails)

`post_detail.html`:
- `newsletter` — `Newsletter`
- `post` — `Post`
- `post_html` — rendered HTML string (Markdown already converted)

`subscribe_confirm.html`:
- `newsletter` — `Newsletter`
- `state` — one of: `"pending"`, `"confirmed"`, `"unsubscribed"`, `"invalid"`

`unsubscribe_confirm.html`:
- `newsletter` — `Newsletter`
- `token` — str
- `state` — one of: `"confirm"`, `"unsubscribed"`, `"invalid"`

`admin/dashboard.html`:
- `newsletter` — `Newsletter`
- `posts` — current page of `Post` NamedTuples (including drafts)
- `next_cursor` — opaque string or None
- `confirmed_count` — int (confirmed only)

`admin/post_form.html`:
- `newsletter` — `Newsletter`
- `post` — `Post` or `None` (None when creating new)
- `error` — string or None

`admin/subscribers.html`:
- `newsletter` — `Newsletter`
- `subscribers` — current page of `Subscriber` NamedTuples
- `next_cursor` — opaque string or None
- `confirmed_count` — int

### url_for in templates

All route endpoints live on the blueprint named `quillet` (default).
If you changed the `name` parameter in `create_blueprint`, use that name instead.

```jinja2
{{ url_for('quillet.post_list', newsletter_slug=newsletter.slug) }}
{{ url_for('quillet.post_detail', newsletter_slug=newsletter.slug, post_slug=post.slug) }}
{{ url_for('quillet.subscribe', newsletter_slug=newsletter.slug) }}
{{ url_for('quillet.confirm_subscription', newsletter_slug=newsletter.slug, token=token) }}
{{ url_for('quillet.unsubscribe_form', newsletter_slug=newsletter.slug, token=token) }}
{{ url_for('quillet.unsubscribe_submit', newsletter_slug=newsletter.slug, token=token) }}
{{ url_for('quillet.dashboard', newsletter_slug=newsletter.slug) }}
{{ url_for('quillet.new_post', newsletter_slug=newsletter.slug) }}
{{ url_for('quillet.edit_post', newsletter_slug=newsletter.slug, post_slug=post.slug) }}
{{ url_for('quillet.subscriber_list', newsletter_slug=newsletter.slug) }}
{{ url_for('quillet.api_create_post', newsletter_slug=newsletter.slug) }}
{{ url_for('quillet.api_publish_post', newsletter_slug=newsletter.slug, post_slug=post.slug) }}
{{ url_for('quillet.api_send_post', newsletter_slug=newsletter.slug, post_slug=post.slug) }}
{{ url_for('quillet.api_list_subscribers', newsletter_slug=newsletter.slug) }}
```

---

## Common mistakes

**Routes return 404 even though the newsletter exists**
The newsletter must be created with `flask quillet create ...` before any routes work.
Run `flask quillet list` to verify it exists.

**Email links (confirm, unsubscribe) point to localhost**
Set `QUILLET_BASE_URL` to your public URL in production, e.g.
`QUILLET_BASE_URL=https://example.com`.

**url_prefix not propagating**
`create_blueprint` returns a single Flask Blueprint. Pass `url_prefix` to
`app.register_blueprint(bp, url_prefix="/newsletter")` — not to `create_blueprint`.
Internal `url_for` calls use the blueprint name, not the prefix.

**Registering multiple newsletters on different prefixes**
Use the `name` parameter to avoid blueprint name collisions:
```python
app.register_blueprint(create_blueprint(..., name="news_a"), url_prefix="/news-a")
app.register_blueprint(create_blueprint(..., name="news_b"), url_prefix="/news-b")
```
Update template `url_for` calls to match (`url_for('news_a.post_list', ...)`).

**Send returns 409**
A post can only be sent once. Use `flask quillet send <slug> <post-slug> --force` to bypass.

**Admin UI missing after setting mode=api**
`api` mode disables the admin UI automatically. Use `mode="web"` and set
`admin_ui=True` (both are defaults) if you want the browser panel.

---

## Running tests

```bash
# Unit-level (Flask test client, in-memory SQLite, no network)
python scripts/smoke_test.py

# MCP Streamable HTTP on ASGI app (tools/list + create_post)
python scripts/mcp_smoke_test.py

# Integration (Docker, real HTTP, real gunicorn)
./scripts/docker_test.sh

# Against a live server (any environment)
QUILLET_TEST_URL=https://example.com \
QUILLET_TEST_SLUG=blog \
QUILLET_ADMIN_PASSWORD=secret \
python scripts/http_smoke_test.py
```

## MCP server (optional extra)

MCP is hosted on the Quillet ASGI process (Streamable HTTP at `/{slug}/mcp/`).
Tools call the same in-process ops as the JSON API. Same Basic Auth (`QUILLET_ADMIN_*`).

```bash
pip install "quillet[mcp]"
flask quillet create "My Blog" --slug=blog --from-email=hi@example.com

QUILLET_ADMIN_PASSWORD=secret \
uvicorn --factory quillet.factory:create_asgi_app --host 127.0.0.1 --port 8000
```

Seed newsletters **before** starting uvicorn (MCP mounts are created at startup).

Agent flow: human gives base URL + password → agent fetches `GET /blog/api/mcp.md` →
writes `.cursor/mcp.json` with `"url": "http://127.0.0.1:8000/blog/mcp/"` + Basic Auth header →
human approves → tools/list.

`flask run` / gunicorn WSGI entry does **not** expose MCP; use uvicorn (or another ASGI server) with `create_asgi_app`.
