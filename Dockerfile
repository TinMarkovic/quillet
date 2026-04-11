FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir ".[all]" gunicorn

COPY quillet/ quillet/
COPY scripts/ scripts/

# Mountable custom templates directory — takes precedence over built-in templates.
# docker run -v ./my-templates:/app/templates/quillet ...
VOLUME ["/app/templates"]

ENV QUILLET_MODE=web
ENV QUILLET_DB_BACKEND=sqlalchemy
ENV QUILLET_DB_URL=sqlite:////data/quillet.db
ENV QUILLET_EMAIL_BACKEND=smtp

EXPOSE 8000

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "2", "quillet._app:application"]
