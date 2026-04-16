import base64
import functools
from collections.abc import Callable
from typing import Any

from flask import Response, current_app, request


def _check_credentials(username: str, password: str) -> bool:
    expected_username = current_app.config.get("QUILLET_ADMIN_USERNAME", "admin")
    expected_password = current_app.config.get("QUILLET_ADMIN_PASSWORD", "")
    return username == expected_username and password == expected_password


def require_basic_auth(view: Callable) -> Callable:
    """Decorator that enforces HTTP Basic Auth using QUILLET_ADMIN_PASSWORD."""

    @functools.wraps(view)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        auth_header = request.headers.get("Authorization", "")

        authenticated = False
        if auth_header.startswith("Basic "):
            try:
                decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                username, _, password = decoded.partition(":")
                authenticated = _check_credentials(username, password)
            except Exception:
                pass

        if not authenticated:
            return Response(
                "Authentication required.",
                status=401,
                headers={"WWW-Authenticate": 'Basic realm="Quillet Admin"'},
            )

        return view(*args, **kwargs)

    return wrapper
