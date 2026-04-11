import base64
import functools
from collections.abc import Callable
from typing import Any

from flask import Response, current_app, request


def _check_credentials(username: str, password: str) -> bool:
    expected_password = current_app.config.get("QUILLET_ADMIN_PASSWORD", "")
    return username == "admin" and password == expected_password


def require_basic_auth(view: Callable) -> Callable:
    """Decorator that enforces HTTP Basic Auth using QUILLET_ADMIN_PASSWORD."""

    @functools.wraps(view)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        auth_header = request.headers.get("Authorization", "")

        if auth_header.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                username, _, password = decoded.partition(":")
                if _check_credentials(username, password):
                    return view(*args, **kwargs)
            except Exception:
                pass

        return Response(
            "Authentication required.",
            status=401,
            headers={"WWW-Authenticate": 'Basic realm="Quillet Admin"'},
        )

    return wrapper
