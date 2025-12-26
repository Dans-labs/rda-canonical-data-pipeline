# rda-canonical-data-pipeline
# Data Ingestion Pipeline

A transaction-safe data pipeline that **cleans, synchronizes, and indexes data** from PostgreSQL and external APIs into Elasticsearch.

This project performs three main stages:

1. **Deduplication & normalization** of existing PostgreSQL data (case-insensitive, audit-safe)
2. **API synchronization** (upsert logic: insert only if record does not already exist)
3. **Elasticsearch ingestion** for fast search and analytics

---

## ✨ Features

- ✅ Case-insensitive deduplication
- ✅ Automatic primary-key detection
- ✅ Transaction-safe cleanup with rollback support
- ✅ Full audit trail (HTML reports)
- ✅ External API data validation and upsert
- ✅ Elasticsearch indexing
- ✅ Idempotent and re-runnable
- ✅ PostgreSQL-first design

---

## 🧱 Architecture Overview
TODO



---

## ⚙️ Requirements

- Python 3.12+
- A PostgreSQL instance (local, Docker, or remote) or use Docker Compose below
- Elasticsearch 8.x


Install and prepare environment (using `uv` package manager described below) or use standard venv/pip:

- Installing `uv` (the minimal steps):

  - Install via pip (works on Linux/macOS/Windows):

  ```bash
  pip install --user uv
  # or, if you use a virtualenv (recommended):
  pip install uv
  ```

  - macOS (Homebrew) option — install Python via Homebrew then install `uv` with pip:

  ```bash
  # install Python if you don't already have it via Homebrew
  brew install python
  # then install uv
  brew search uv
  brew info uv
  brew install uv
  ```

- With `uv`:

```bash
uv venv .venv
uv sync --frozen --no-cache
```

- Standard venv/pip alternative:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Run the development server with autoreload:

```bash
# from the repository root
make run-dev
# or directly
.venv/bin/uvicorn src.main:app --reload --host 0.0.0.0 --port 1912
```

API docs will be available at: `http://localhost:1912/docs`

---

## Docker / Compose (local integration)

The repository includes a `Dockerfile` and `docker-compose.yaml` to run the service alongside a Postgres and MailDev instance for local testing.

Start services with:

```bash
# builds the  rda-canonical-data-pipeline image and starts containers
docker-compose up -d --build
```

## Configuration

Configuration is loaded via Dynaconf from `conf/*.toml` and environment variables. Copy and customize the example config before running in production:

```bash
cp conf/settings.example.toml conf/settings.toml
# or use conf/settings.production.toml as a template
```

Important environment variables (examples):

- `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT`, `DB_NAME` — PostgreSQL connection
- `MAIL_HOST`, `MAIL_PORT`, `MAIL_FROM`, `MAIL_TO` — SMTP settings for notifications (MailDev available for local testing)
- `API_PREFIX` — API route prefix, e.g. `/api/v1`
- `EXPOSE_PORT` — HTTP port (default: 1912)

For local development the repository includes `.env.example` (copy to `.env`) and `conf/settings.example.toml`.

