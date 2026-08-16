FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --system terstars \
    && useradd --system --gid terstars --home-dir /app --shell /usr/sbin/nologin terstars

WORKDIR /app

COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY --chown=terstars:terstars app ./app
COPY --chown=terstars:terstars migrations ./migrations
COPY --chown=terstars:terstars scripts ./scripts
COPY --chown=terstars:terstars alembic.ini pyproject.toml ./
RUN mkdir -p /app/sessions && chown terstars:terstars /app/sessions

USER terstars

HEALTHCHECK --interval=30s --timeout=8s --start-period=20s --retries=3 \
    CMD ["python", "scripts/healthcheck.py"]

CMD ["python", "-m", "app.main"]
