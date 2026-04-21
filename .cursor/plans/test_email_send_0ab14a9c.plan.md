---
name: Test Email Send
overview: Add a "Send test email" action to the post editor that delivers the post to a single configurable address, with the subject prefixed "[TEST]". The default address is newsletter.reply_to (falling back to newsletter.from_email) — no new config fields needed.
todos:
  - id: send-test-route
    content: Add send-test POST route and pass newsletter to edit_post renders in quillet/admin.py
    status: completed
  - id: post-form-ui
    content: Add test send section to quillet/templates/quillet/admin/post_form.html
    status: completed
isProject: false
---

# Test Email Send

## What changes

**2 files touched, no model or schema changes.**

---

## 1. New admin route

### `[quillet/admin.py](quillet/admin.py)`

Add `POST /<newsletter_slug>/admin/posts/<post_slug>/send-test`.

Logic:

1. Load newsletter + post (404 if missing)
2. Read `test_email` from form; fall back to `newsletter.reply_to or newsletter.from_email` if blank
3. Build a temporary `Subscriber(id=0, newsletter_id=..., email=test_email, token="test", confirmed_at=None)`
4. Fetch config; build a test config using `config._replace(subject_prefix=f"[TEST] {config.subject_prefix or ''}".strip())`
5. Call `_email().send_post(newsletter, post, [test_subscriber], unsubscribe_url_template, test_config)`
6. Redirect back to `edit_post` with `?test_sent=1`

The `edit_post` and `new_post` GET renderers already pass `newsletter` to the template — no extra context needed, since the default email comes straight from `newsletter.reply_to or newsletter.from_email`.

---

## 2. Post form UI

### `[quillet/templates/quillet/admin/post_form.html](quillet/templates/quillet/admin/post_form.html)`

Add a "Send test" section below the existing `.post-actions` div, shown only when `post` exists:

```html
{% if post %}
<div class="test-send">
  <form method="post" action="{{ url_for('quillet.send_test_post', ...) }}">
    <label for="test_email">Send test email to</label>
    <div style="display:flex;gap:0.5rem;">
      <input type="email" id="test_email" name="test_email"
             value="{{ newsletter.reply_to or newsletter.from_email }}">
      <button type="submit" class="btn">Send test</button>
    </div>
    {% if test_sent %}<p class="hint" style="color:#1a7f37">Test email sent.</p>{% endif %}
  </form>
</div>
{% endif %}
```

Small style addition: `input[type=email]` styled same as `input[type=text]`.