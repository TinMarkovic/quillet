#!/usr/bin/env python3
"""
http_smoke_test.py — HTTP smoke tests against a live QUILLET instance.

Can be run against any running server (local, Docker, staging):

    python scripts/http_smoke_test.py

Environment variables:
    QUILLET_TEST_URL      Base URL of the server (default: http://127.0.0.1:8000)
    QUILLET_TEST_SLUG     Newsletter slug to test against (default: test)
    QUILLET_ADMIN_PASSWORD  Admin password (default: testpassword)
"""

import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE_URL = os.environ.get("QUILLET_TEST_URL", "http://127.0.0.1:8000").rstrip("/")
SLUG = os.environ.get("QUILLET_TEST_SLUG", "test")
PASSWORD = os.environ.get("QUILLET_ADMIN_PASSWORD", "testpassword")


# ---------------------------------------------------------------------------
# Minimal HTTP client (stdlib only)
# ---------------------------------------------------------------------------


def _auth_header() -> dict:
    creds = base64.b64encode(f"admin:{PASSWORD}".encode()).decode()
    return {"Authorization": f"Basic {creds}"}


def req(
    method: str,
    path: str,
    *,
    form: dict | None = None,
    body: dict | None = None,
    headers: dict | None = None,
) -> tuple[int, dict | str]:
    url = f"{BASE_URL}{path}"
    all_headers = dict(headers or {})

    if body is not None:
        data = json.dumps(body).encode()
        all_headers["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        all_headers["Content-Type"] = "application/x-www-form-urlencoded"
    else:
        data = None

    request = urllib.request.Request(url, data=data, headers=all_headers, method=method)
    try:
        with urllib.request.urlopen(request) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except ValueError:
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, raw


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def check(label: str, got: int, expected: int) -> bool:
    ok = got == expected
    print(f"  {'OK  ' if ok else 'FAIL'} {label}: HTTP {got}" + ("" if ok else f" (expected {expected})"))
    return ok


# ---------------------------------------------------------------------------
# Test suites
# ---------------------------------------------------------------------------


def test_public(failures: list) -> None:
    print("\n-- Public routes --")
    failures += [1] if not check("archive", req("GET", f"/{SLUG}/")[0], 200) else []
    failures += [1] if not check("unknown newsletter 404", req("GET", "/does-not-exist/")[0], 404) else []
    failures += [1] if not check("unpublished post 404", req("GET", f"/{SLUG}/posts/no-such-post")[0], 404) else []


def test_subscribe(failures: list) -> tuple[str | None]:
    """Returns the confirm token extracted from the API response, or None."""
    print("\n-- Subscribe flow --")

    status, _ = req("POST", f"/{SLUG}/subscribe", form={"email": "docker@example.com"})
    failures += [1] if not check("subscribe (noop email)", status, 200) else []

    # Fetch token via admin API so we can confirm without needing real email.
    status, data = req("GET", f"/{SLUG}/api/subscribers", headers=_auth_header())
    failures += [1] if not check("list subscribers", status, 200) else []

    token = None
    if isinstance(data, dict) and data.get("subscribers"):
        token = data["subscribers"][-1]["token"]

    if token:
        status, _ = req("GET", f"/{SLUG}/confirm/{token}")
        failures += [1] if not check("confirm subscription", status, 200) else []

    status, _ = req("GET", f"/{SLUG}/confirm/bad-token")
    failures += [1] if not check("invalid confirm token → 200 (invalid state)", status, 200) else []

    return token


def test_admin_auth(failures: list) -> None:
    print("\n-- Admin auth --")
    failures += [1] if not check("no auth → 401", req("GET", f"/{SLUG}/admin/")[0], 401) else []
    bad_auth = {"Authorization": "Basic " + base64.b64encode(b"admin:wrong").decode()}
    failures += (
        [1] if not check("wrong password → 401", req("GET", f"/{SLUG}/admin/", headers=bad_auth)[0], 401) else []
    )
    failures += (
        [1] if not check("correct auth → 200", req("GET", f"/{SLUG}/admin/", headers=_auth_header())[0], 200) else []
    )


def test_post_lifecycle(failures: list) -> None:
    print("\n-- Post lifecycle --")
    auth = _auth_header()

    status, data = req(
        "POST",
        f"/{SLUG}/api/posts",
        body={"title": "Docker Test Post", "slug": "docker-test-post", "body_md": "# Hello\n\nFrom Docker."},
        headers=auth,
    )
    failures += [1] if not check("create post", status, 201) else []

    status, _ = req("POST", f"/{SLUG}/api/posts/docker-test-post/publish", headers=auth)
    failures += [1] if not check("publish", status, 200) else []

    failures += [1] if not check("post detail", req("GET", f"/{SLUG}/posts/docker-test-post")[0], 200) else []
    failures += [1] if not check("archive has post", req("GET", f"/{SLUG}/")[0], 200) else []

    status, _ = req("POST", f"/{SLUG}/api/posts/docker-test-post/send", headers=auth)
    failures += [1] if not check("send post", status, 200) else []

    status, _ = req("POST", f"/{SLUG}/api/posts/docker-test-post/send", headers=auth)
    failures += [1] if not check("double send → 409", status, 409) else []


def test_admin_ui(failures: list) -> None:
    print("\n-- Admin UI --")
    auth = _auth_header()
    failures += [1] if not check("dashboard", req("GET", f"/{SLUG}/admin/", headers=auth)[0], 200) else []
    failures += [1] if not check("new post form", req("GET", f"/{SLUG}/admin/posts/new", headers=auth)[0], 200) else []
    failures += (
        [1]
        if not check("edit post form", req("GET", f"/{SLUG}/admin/posts/docker-test-post/edit", headers=auth)[0], 200)
        else []
    )
    failures += (
        [1] if not check("subscriber list", req("GET", f"/{SLUG}/admin/subscribers", headers=auth)[0], 200) else []
    )


def test_json_mode(failures: list) -> None:
    print("\n-- JSON accept header --")
    status, data = req("GET", f"/{SLUG}/", headers={"Accept": "application/json"})
    failures += [1] if not check("archive as JSON", status, 200) else []
    if isinstance(data, dict):
        failures += [1] if not check("response has 'posts' key", 200 if "posts" in data else 0, 200) else []
    else:
        failures.append(1)
        print("  FAIL  expected JSON object, got string")


def test_unsubscribe(failures: list, token: str | None) -> None:
    print("\n-- Unsubscribe --")
    if token:
        failures += [1] if not check("unsubscribe", req("GET", f"/{SLUG}/unsubscribe/{token}")[0], 200) else []
    else:
        print("  SKIP  (no token available)")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def run() -> int:
    print(f"QUILLET HTTP smoke test → {BASE_URL}  newsletter={SLUG!r}")

    failures: list = []

    test_public(failures)
    token = test_subscribe(failures)
    test_admin_auth(failures)
    test_post_lifecycle(failures)
    test_admin_ui(failures)
    test_json_mode(failures)
    test_unsubscribe(failures, token)

    total = sum(failures)
    print(f"\n{'All checks passed.' if not total else f'{total} check(s) FAILED.'}")
    return total


if __name__ == "__main__":
    sys.exit(run())
