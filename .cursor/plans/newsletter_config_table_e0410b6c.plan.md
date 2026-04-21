---
name: Newsletter Config Table
overview: Add a `newsletter_config` DB table and an Admin UI settings page to manage newsletter identity fields and email template customisation (opener, footer, subject prefix) without touching environment variables.
todos:
  - id: models
    content: Add NewsletterConfig NamedTuple to quillet/models.py
    status: completed
  - id: db-schema
    content: Add _newsletter_config table and get/save/update_newsletter methods to quillet/db/sqlalchemy.py
    status: completed
  - id: db-protocol
    content: Extend NewsletterRepository Protocol in quillet/db/__init__.py
    status: completed
  - id: email-protocol
    content: Add optional config param to EmailSender Protocol in quillet/email/__init__.py
    status: completed
  - id: email-senders
    content: Update smtp.py and mailgun.py to use config (opener, footer, subject_prefix, fix from_name); update noop.py signature
    status: completed
  - id: admin-routes
    content: Add GET/POST settings routes to quillet/admin.py; pass config in send route
    status: completed
  - id: routes-confirmation
    content: Fetch and pass config in send_confirmation call in quillet/routes.py
    status: completed
  - id: settings-template
    content: Create quillet/templates/quillet/admin/settings.html with the two fieldsets
    status: completed
  - id: dashboard-link
    content: Add Settings link to quillet/templates/quillet/admin/dashboard.html
    status: completed
isProject: false
---

# Newsletter Config Table & Admin Settings

## What gets managed

**Newsletter identity** (already in `newsletters` table, made editable via new form):

- `name`, `from_name`, `from_email`, `reply_to`

**New per-newsletter config** (new `newsletter_config` table):

- `subject_prefix` — replaces/overrides `QUILLET_SUBJECT_PREFIX` env var
- `email_opener` — markdown template shown above the confirm link in subscription confirmation emails; supports `{newsletter_name}` and `{confirm_url}` variables
- `email_footer` — markdown template appended to post emails; supports `{unsubscribe_url}` variable

Both templates fall back to the current hard-coded defaults when not set.

---

## Data layer

### `[quillet/models.py](quillet/models.py)`

Add a `NewsletterConfig` NamedTuple and a `CONFIG_DEFAULTS` dict of known keys with their defaults:

```python
class NewsletterConfig(NamedTuple):
    newsletter_id: int
    subject_prefix: str | None
    email_opener: str | None
    email_footer: str | None

CONFIG_DEFAULTS: dict[str, str | None] = {
    "subject_prefix": None,
    "email_opener": None,
    "email_footer": None,
}
```

### `[quillet/db/sqlalchemy.py](quillet/db/sqlalchemy.py)`

Add a `newsletter_config` KV table — one row per config entry per newsletter. Schema is **fixed forever**; adding new config options never requires a schema change:

```python
_newsletter_config = Table(
    "newsletter_config", metadata,
    Column("newsletter_id", Integer, ForeignKey("newsletters.id"), nullable=False),
    Column("key", String(128), nullable=False),
    Column("value", Text, nullable=True),
    UniqueConstraint("newsletter_id", "key"),
)
```

Add three methods to `SQLAlchemyRepository`:

- `get_newsletter_config(newsletter_id: int) -> NewsletterConfig` — fetches all rows for the newsletter, merges with `CONFIG_DEFAULTS`, returns assembled `NewsletterConfig`
- `save_newsletter_config(config: NewsletterConfig) -> None` — upserts one row per key (INSERT OR REPLACE / ON CONFLICT DO UPDATE)
- `update_newsletter(newsletter_id, name, from_name, from_email, reply_to) -> Newsletter`

### `[quillet/db/__init__.py](quillet/db/__init__.py)`

Add the three new method signatures to the `NewsletterRepository` Protocol.

---

## Email layer

### `[quillet/email/__init__.py](quillet/email/__init__.py)`

Add `config: NewsletterConfig | None = None` as an optional parameter to both `send_confirmation` and `send_post` in the `EmailSender` Protocol.

### `[quillet/email/smtp.py](quillet/email/smtp.py)` & `[quillet/email/mailgun.py](quillet/email/mailgun.py)`

- Accept `config` in both send methods
- Use `config.subject_prefix` (falling back to env value) for subject lines
- Render `config.email_opener` through `md_to_html`/`md_to_plain` for confirmation emails, falling back to current hard-coded copy; supports `{newsletter_name}` and `{confirm_url}` template variables
- Render `config.email_footer` similarly for post emails, interpolating `{unsubscribe_url}`; falls back to current hard-coded `---\nUnsubscribe:` copy
- Fix SMTP: currently uses env `from_name`/`from_email` for the From header instead of `newsletter.from_name`/`newsletter.from_email` — align it with Mailgun's behaviour

### `[quillet/email/noop.py](quillet/email/noop.py)`

Accept `config` param (no-op, just for protocol compliance).

---

## Admin layer

### `[quillet/admin.py](quillet/admin.py)`

Add two routes (alongside existing ones, same `@require_basic_auth`):

- `GET /<newsletter_slug>/admin/settings` — load newsletter + config, render form
- `POST /<newsletter_slug>/admin/settings` — validate, call `update_newsletter` + `save_newsletter_config`, redirect back

Also update the `send` route to fetch config and pass it to `_email().send_post(... config=config)`, and similarly in `send_confirmation` in `[quillet/routes.py](quillet/routes.py)`.

### New `[quillet/templates/quillet/admin/settings.html](quillet/templates/quillet/admin/settings.html)`

Simple form with fieldsets:

**Newsletter identity:** Name, From Name, From Email, Reply-To

**Email templates:** Subject Prefix, Email Opener (textarea + hint about `{newsletter_name}`, `{confirm_url}`), Email Footer (textarea + hint about `{unsubscribe_url}`)

### `[quillet/templates/quillet/admin/dashboard.html](quillet/templates/quillet/admin/dashboard.html)`

Add a "Settings" link next to existing nav.

---

## No-migration strategy

The `newsletter_config` table is a **new KV table** created by `create_all()` on first startup — no `ALTER TABLE` anywhere. Adding a new config option in future means adding an entry to `CONFIG_DEFAULTS` and a field to `NewsletterConfig` in Python; the DB schema never needs to change. Missing keys are transparently filled from defaults at read time.