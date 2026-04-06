# System Setup

## Environment
- Copy `.env.example` to `.env`.
- Set `ADMIN_USER`, `ADMIN_PASS`, `ADMIN_SESSION_TTL_MINUTES`.
- Keep `ALLOW_STATIC_ADMIN_TOKEN=0` for production.
- Set `GIGACHAT_AUTH_KEY` and keep `GIGACHAT_INSECURE_SSL_FALLBACK=0`.

## Security Defaults
- Admin auth uses short-lived server sessions (`admin_session` table).
- Passwords are stored as `pbkdf2_sha256` hashes.
- Legacy SHA-256 hashes are auto-upgraded on successful login.

## Startup
- `docker compose build && docker compose up -d`
- Health check: `GET /api/health`
- Admin login: `POST /api/v1/admin/login`

## RAG / LLM
- Knowledge base source: `knowledge_article`.
- Retrieval mode: lexical scoring with metadata boosts.
- Debug mode for chat: send `"debug": true` in chat payload.

## Notes
- Standard deploy is non-destructive (no automatic volume wipe).
- Use dedicated cleanup script only for explicit reset scenarios.
