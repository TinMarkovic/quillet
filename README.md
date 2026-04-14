# QUILLET

Flask newsletter + microblog package. Three deployment modes, pluggable backends, minimal deps.

```
pip install "quillet[sqlalchemy,mailgun]"
```

---

## Three modes

| Mode | When to use |
|---|---|
| **Blueprint** | Add newsletters to an existing Flask app |
| **Standalone web** | Run as a separate Docker service, proxy via nginx |
| **Headless API** | JSON-only backend; bring your own frontend |

---

## Mode 1 — Flask Blueprint

```python
from quillet import create_blueprint, get_or_create_newsletter
from quillet.db.sqlalchemy import SQLAlchemyRepository
from quillet.email.mailgun import MailgunSender

db = SQLAlchemyRepository("sqlite:///newsletter.db")

# Seed your newsletter on first run — safe to call on every startup.
get_or_create_newsletter(db, slug="blog", name="My Blog", from_email="hi@example.com")

app.register_blueprint(
    create_blueprint(
        db=db,
        email=MailgunSender(api_key="...", domain="..."),
        admin_password="secret",
    ),
    url_prefix="/newsletter",
)
```

All routes are now available at `/newsletter/<newsletter_slug>/`. Visit `http://localhost:5000/newsletter/blog/`.

You can also seed from the CLI instead (idempotent — safe to re-run):

```bash
export FLASK_APP=your_app
flask quillet create "My Blog" --slug=blog --from-email=hi@example.com
```

### Blueprint options

```python
create_blueprint(
    db=...,
    email=...,
    admin_password="secret",
    admin_username="admin",  # Basic Auth username (default: "admin")
    mode="web",              # "web" (default) or "api" — disables HTML routes
    admin_ui=True,           # set False to disable the browser admin UI
    base_url="",             # override for email links (confirm, unsubscribe)
    name="quillet",          # blueprint name; change if you register multiple instances
)
```

---

## Mode 2 — Standalone Docker (web)

```bash
docker compose up
```

Visit `http://localhost:8000`. Configure via environment variables:

| Variable | Default | Description |
|---|---|---|
| `QUILLET_MODE` | `web` | `web` or `api` |
| `QUILLET_ADMIN_PASSWORD` | *(required)* | Basic Auth password |
| `QUILLET_ADMIN_USERNAME` | `admin` | Basic Auth username |
| `QUILLET_ADMIN_UI` | `true` | Set `false` to disable the browser admin panel |
| `QUILLET_BASE_URL` | *(host URL)* | Public URL used in email links — set this in production |
| `QUILLET_DB_BACKEND` | `sqlalchemy` | `sqlalchemy` or `supabase_rest` |
| `QUILLET_DB_URL` | `sqlite:////data/quillet.db` | SQLAlchemy connection string |
| `QUILLET_SUPABASE_URL` | — | Supabase project URL |
| `QUILLET_SUPABASE_KEY` | — | Supabase anon key |
| `QUILLET_EMAIL_BACKEND` | `smtp` | `mailgun`, `smtp`, or `noop` |
| `QUILLET_MAILGUN_API_KEY` | — | Mailgun private API key |
| `QUILLET_MAILGUN_DOMAIN` | — | Mailgun sending domain |
| `QUILLET_MAILGUN_REGION` | `us` | Mailgun region — `us` or `eu` |
| `QUILLET_MAILGUN_SENDER_EMAIL` | `quillet@<domain>` | Envelope From address (must be on your Mailgun domain for SPF/DKIM to pass) |
| `QUILLET_SUBJECT_PREFIX` | *(empty)* | Prepended to every post subject line, e.g. `[My Blog] ` |
| `QUILLET_SMTP_HOST` | `localhost` | SMTP host |
| `QUILLET_SMTP_PORT` | `587` | SMTP port |
| `QUILLET_SMTP_USE_TLS` | `true` | Set `false` for local dev (e.g. Mailhog) |
| `QUILLET_SMTP_USERNAME` | — | SMTP username (optional) |
| `QUILLET_SMTP_PASSWORD` | — | SMTP password (optional) |
| `QUILLET_SMTP_FROM_EMAIL` | *(required)* | Sender email address |
| `QUILLET_SMTP_FROM_NAME` | — | Sender display name |

Create a newsletter after starting (idempotent):

```bash
docker compose exec quillet flask quillet create "My Blog" --slug=blog --from-email=hi@example.com
```

---

## Mode 3 — Headless API

Set `QUILLET_MODE=api` (and `QUILLET_ADMIN_UI=false`). All endpoints return JSON only; no HTML templates are rendered.

---

## Routes

All routes are prefixed with `/<newsletter_slug>/`.

### Public

| Method | Path | Description |
|---|---|---|
| `GET` | `/<slug>/` | Post archive. Returns HTML or JSON (`Accept: application/json`). |
| `GET` | `/<slug>/posts/<post_slug>` | Single post. Returns HTML or JSON. |
| `POST` | `/<slug>/subscribe` | Subscribe. Accepts form `email` or JSON `{"email": "..."}`. Sends double opt-in email. |
| `GET` | `/<slug>/confirm/<token>` | Confirm subscription via emailed link. |
| `GET` | `/<slug>/unsubscribe/<token>` | Unsubscribe via emailed link. |
| `GET` | `/<slug>/feed.xml` | RSS 2.0 feed of published posts. |

### Admin browser UI (Basic Auth)

| Method | Path | Description |
|---|---|---|
| `GET` | `/<slug>/admin/` | Dashboard — post list with status badges, subscriber count. |
| `GET/POST` | `/<slug>/admin/posts/new` | Create post form. |
| `GET/POST` | `/<slug>/admin/posts/<post_slug>/edit` | Edit post. Publish and Send buttons are inline. |
| `POST` | `/<slug>/admin/posts/<post_slug>/publish` | Publish (sets `published_at`). |
| `POST` | `/<slug>/admin/posts/<post_slug>/send` | Send to all confirmed subscribers. Idempotent — a sent post cannot be re-sent. |
| `POST` | `/<slug>/admin/posts/<post_slug>/delete` | Delete a post. Warns if the post was already sent. |
| `GET` | `/<slug>/admin/subscribers` | Subscriber list with confirmation status. |
| `POST` | `/<slug>/admin/subscribers/<id>/delete` | Hard-delete a subscriber. |

### JSON API (Basic Auth)

| Method | Path | Body / Response |
|---|---|---|
| `POST` | `/<slug>/api/posts` | `{"title", "slug", "body_md"}` → `{"post": {...}}` |
| `POST` | `/<slug>/api/posts/<post_slug>/publish` | → `{"post": {...}}` |
| `POST` | `/<slug>/api/posts/<post_slug>/send` | → `{"ok": true, "recipients": N}` |
| `DELETE` | `/<slug>/api/posts/<post_slug>` | → `{"ok": true}` |
| `GET` | `/<slug>/api/subscribers` | → `{"subscribers": [...]}` |
| `DELETE` | `/<slug>/api/subscribers/<id>` | → `{"ok": true}` |

---

## CLI

```bash
# Create a newsletter (idempotent — safe to re-run)
flask quillet create "My Blog" --slug=blog --from-email=hi@example.com --from-name="My Blog"

# List all newsletters
flask quillet list

# Send a published post
flask quillet send blog my-post-slug

# Force re-send (already sent posts are blocked by default)
flask quillet send blog my-post-slug --force

# List subscribers
flask quillet subscribers blog
```

---

## Custom backends

Implement the `NewsletterRepository` or `EmailSender` protocol — no base classes, just matching method signatures.

### Custom DB backend

```python
from quillet.db import NewsletterRepository
from quillet.models import Newsletter, Post, Subscriber

class MyRepository:
    def get_newsletter(self, slug: str) -> Newsletter | None: ...
    def list_posts(self, newsletter_slug: str, published_only: bool = True) -> list[Post]: ...
    def get_post(self, newsletter_slug: str, post_slug: str) -> Post | None: ...
    def create_post(self, newsletter_slug: str, title: str, slug: str, body_md: str) -> Post: ...
    def update_post(self, post_id: int, title: str, slug: str, body_md: str) -> Post: ...
    def publish_post(self, post_id: int) -> Post: ...
    def mark_sent(self, post_id: int) -> None: ...
    def delete_post(self, post_id: int) -> None: ...
    def add_subscriber(self, newsletter_slug: str, email: str, token: str) -> Subscriber: ...
    def confirm_subscriber(self, token: str) -> Subscriber | None: ...
    def list_confirmed_subscribers(self, newsletter_slug: str) -> list[Subscriber]: ...
    def list_all_subscribers(self, newsletter_slug: str) -> list[Subscriber]: ...
    def unsubscribe(self, token: str) -> None: ...
    def delete_subscriber(self, subscriber_id: int) -> None: ...
    def create_newsletter(self, slug: str, name: str, from_email: str, from_name: str, reply_to: str | None) -> Newsletter: ...
```

### Custom email backend

```python
from quillet.email import EmailSender
from quillet.models import Newsletter, Post, Subscriber

class MyEmailSender:
    def send_confirmation(
        self,
        newsletter: Newsletter,
        subscriber: Subscriber,
        confirm_url: str,
    ) -> None: ...

    def send_post(
        self,
        newsletter: Newsletter,
        post: Post,
        subscribers: list[Subscriber],
        unsubscribe_url_template: str,  # contains {token} placeholder
    ) -> None: ...
```

---

## Template overrides

All built-in templates live at `quillet/templates/quillet/`. Flask resolves templates in this order: **app templates → blueprint templates**. Drop an override anywhere Flask finds templates first.

**Blueprint mode** — add to your app's `templates/` directory:

```
templates/
└── quillet/
    ├── base.html
    ├── post_list.html
    ├── post_detail.html
    ├── subscribe_confirm.html
    └── admin/
        ├── dashboard.html
        ├── post_form.html
        └── subscribers.html
```

`base.html` exposes `{% block head %}` (inside `<head>`) and `{% block content %}` for easy integration with a parent layout.

**Standalone Docker** — mount a volume:

```yaml
volumes:
  - ./my-templates:/app/templates/quillet
```

### Template context variables

| Template | Variables |
|---|---|
| `post_list.html` | `newsletter`, `posts`, `subscribe_error` (optional) |
| `post_detail.html` | `newsletter`, `post`, `post_html` (rendered HTML string) |
| `subscribe_confirm.html` | `newsletter`, `state` (`pending`/`confirmed`/`unsubscribed`/`invalid`) |
| `admin/dashboard.html` | `newsletter`, `posts`, `subscriber_count`, `confirmed_count` |
| `admin/post_form.html` | `newsletter`, `post` (None if new), `error` (optional) |
| `admin/subscribers.html` | `newsletter`, `subscribers` |

All model fields are accessible as attributes (e.g. `newsletter.name`, `post.title`, `post.published_at`).

### Template blocks

`base.html` defines the following overridable blocks:

| Block | Default | Notes |
|---|---|---|
| `title` | `newsletter.name` | Also used as `og:title` |
| `description` | *(empty)* | Populates `<meta name="description">` and OG/Twitter tags when non-empty |
| `canonical` | *(empty)* | Populates `<link rel="canonical">` and `og:url` when non-empty |
| `og_type` | `website` | Override to `article` on post pages |
| `head` | *(empty)* | Extra content inside `<head>` (styles, scripts) |
| `content` | *(empty)* | Page body |

`post_detail.html` sets `description` (first 160 chars of body text), `canonical` (absolute post URL), and `og_type` (`article`) automatically.

A `wordcount` Jinja2 filter is registered on the app, so you can use reading-time estimates in any template override:

```html
{{ [1, ((post_html | striptags | wordcount) / 200) | round | int] | max }} min read
```

---

## Development email backend

Use `noop` to skip all email sending — confirmation links are printed to logs instead:

```python
from quillet.email.noop import NoopSender

create_blueprint(db=..., email=NoopSender(), admin_password="...")
```

Or via env var: `QUILLET_EMAIL_BACKEND=noop`

---

## Testing

```bash
# Fast: Flask test client, no server required
python scripts/smoke_test.py

# Full: build Docker image + one-shot container test
./scripts/docker_test.sh

# Skip rebuild
./scripts/docker_test.sh --no-build

# HTTP tests against any running instance
QUILLET_TEST_URL=https://your-server.com \
QUILLET_TEST_SLUG=blog \
QUILLET_ADMIN_PASSWORD=secret \
python scripts/http_smoke_test.py
```
