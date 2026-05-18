# Codex Project Context

This file is the short repo-local memory for new Codex sessions. Read it first,
then open the detailed docs only when the task needs deeper context.

## Project

AgroLead Assistant v6 is a B2B grain-trading assistant and back-office prototype.

Core stack:
- Backend: FastAPI + SQLModel in `backend/app`.
- Frontend: static HTML/CSS/JS served by Nginx in `web`.
- Database: PostgreSQL in Docker Compose; SQLite is used by tests/fallback code.
- LLM: GigaChat is used for response wording, not as the source of business truth.
- Deploy: Docker Compose + `deploy.sh` smoke checks.
- Public domain: `https://artemshtodin.ru`.

Keep changes small and sympathetic to the existing code. This is a legacy-ish
monolith, not a rewrite target.

## First Files To Read

- `AGENTS.md`: this context.
- `README.md`: quick start and docs map.
- `docker-compose.yml`: runtime topology.
- `deploy.sh`: production deploy and smoke checks.
- `docs/06-deploy-and-ops.md`: deploy/runbook details.
- `docs/05-api.md`: API contracts.
- `backend/app/main.py`: main FastAPI app and orchestration.
- `backend/app/models.py`: SQLModel schema.
- `backend/app/seed.py`: default reference data and admin bootstrap.

## Runtime Topology

Compose services:
- `db`: PostgreSQL 16, internal only, persistent volume `pg_data`.
- `api`: FastAPI on container port `8000`, host-bound only to `127.0.0.1:8000`.
- `webui`: Nginx reverse proxy, public `80` and `443`.
- `certbot`: Let's Encrypt renewal loop.

TLS:
- Nginx serves ACME HTTP-01 challenge from `/var/www/certbot`.
- Certificates live in the persistent Docker volume `letsencrypt`.
- `certbot_www` stores challenge files.
- Legacy `ssl/fullchain.pem` and `ssl/privkey.key` are not required.

Important security posture:
- Only ports `80` and `443` should be public.
- Do not publish PostgreSQL or API directly to the internet.
- Never commit `.env`, private keys, cert dumps, tokens, DB dumps, or passwords.
- `.env.example` and `env.example` intentionally leave secrets blank.

## Environment

Required server/local env keys:
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`
- `DATABASE_URL`
- `ADMIN_USER`, `ADMIN_PASS`
- `DOMAIN_NAME=artemshtodin.ru`
- optional `CERTBOT_EMAIL`, `CERTBOT_STAGING`
- `GIGACHAT_AUTH_KEY` if real LLM responses are needed

`deploy.sh` can generate missing local values for `POSTGRES_PASSWORD` and
`ADMIN_PASS` into `.env`. It also clears legacy `GIGACHAT_CA_FILE=/ssl/...`
paths that were valid before the Let's Encrypt volume migration.

## Commands

Local checks:

```bash
python -m compileall -q backend/app
python -m unittest discover -s backend/tests -v
docker compose config --quiet
```

Deploy on the server:

```bash
cd /root/agrolead-assistant
git fetch origin dev
git pull --ff-only origin dev
bash ./deploy.sh
```

Post-deploy checks:

```bash
curl -I http://artemshtodin.ru
curl -I https://artemshtodin.ru
curl https://artemshtodin.ru/api/health
docker compose ps
docker compose logs --tail=100
docker compose run --rm --entrypoint certbot certbot certificates
```

Expected public behavior:
- `http://artemshtodin.ru` returns `301` to HTTPS.
- `https://artemshtodin.ru` returns `200` or app-level redirect without SSL error.
- `/api/health` returns JSON with `"status": "ok"` and `"db_ok": true`.

## Current Production Snapshot

Last verified: 2026-05-18.

- Branch on server: `dev`.
- Server project path: `/root/agrolead-assistant`.
- Public IP for `artemshtodin.ru`: `144.31.57.4`.
- HTTPS certificate: Let's Encrypt for `artemshtodin.ru`, valid until
  2026-08-16.
- Containers after successful deploy:
  - `agrolead-webui`: public `80/443`
  - `agrolead-api`: `127.0.0.1:8000`
  - `agrolead-db`: internal `5432`
  - `agrolead-certbot`: renewal loop

Known non-fatal issue:
- GigaChat may log SSL verification failures on the server if its upstream chain
  is not trusted by the container CA bundle. The app falls back and remains
  healthy, but real LLM behavior may need CA configuration or an explicit,
  reviewed `GIGACHAT_INSECURE_SSL_FALLBACK=1`.

## Testing Notes

- Tests use SQLite files and set fake GigaChat credentials.
- Tests explicitly call `startup()` because current TestClient usage does not
  automatically run lifespan in all local environments.
- `backend/app/models.py` uses `from __future__ import annotations` to keep
  SQLModel/Pydantic compatible with newer local Python versions.

## Git Workflow

- Work on `dev`.
- Keep commits atomic.
- Before commit, run relevant tests and `git diff --check`.
- Scan diffs for secrets before pushing.
- Push to `origin dev` when the requested repo change should persist.

## Documentation Policy

Keep this file short. If the context grows beyond quick-start memory, move the
detail into `docs/` and leave only the pointer here.
