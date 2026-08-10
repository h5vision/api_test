FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN groupadd --gid 10001 vision \
    && useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin vision

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN python -m pip install --no-cache-dir -r requirements.txt

COPY backend ./backend
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini

RUN mkdir -p /shared/uploads /shared/projects /shared/embedding-results /tmp/vision \
    && chown -R vision:vision /shared /tmp/vision /app

USER 10001:10001
EXPOSE 8000

CMD ["granian", "--interface", "asgi", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--log-level", "info", "--access-log", "backend.asgi:app"]
