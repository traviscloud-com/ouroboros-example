FROM python:3.12-slim

WORKDIR /srv/ouroboros

COPY pyproject.toml ./
COPY app ./app
COPY migrations ./migrations
# Migrations are applied on start (design D11); the wheel carries them under
# app/migrations and the source copy above keeps the layout identical to the repo.
RUN pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 ouroboros
USER ouroboros

EXPOSE 8000
CMD ["uvicorn", "app.asgi:app", "--host", "0.0.0.0", "--port", "8000"]
