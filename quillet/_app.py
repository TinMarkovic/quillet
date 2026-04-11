"""WSGI entrypoint for gunicorn: gunicorn quillet._app:application"""

from .factory import create_app

application = create_app()
