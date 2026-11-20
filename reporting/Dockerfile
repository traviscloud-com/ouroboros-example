# Build context is the repo root: docker build -f reporting/Dockerfile .
FROM node:20-alpine AS spa
WORKDIR /build
COPY dashboard/package.json dashboard/package-lock.json ./
RUN npm ci --no-fund --no-audit
COPY dashboard/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /srv/ouroboros

COPY reporting/pyproject.toml ./
COPY reporting/app ./app
RUN pip install --no-cache-dir '.[parquet]'
COPY --from=spa /build/dist ./static

RUN useradd --create-home --uid 10001 ouroboros
USER ouroboros

ENV STATIC_DIR=/srv/ouroboros/static
EXPOSE 8000
CMD ["uvicorn", "app.asgi:app", "--host", "0.0.0.0", "--port", "8000"]
