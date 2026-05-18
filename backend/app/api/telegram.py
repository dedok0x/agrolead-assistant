from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/integrations/telegram", tags=["telegram"])

# The concrete route is registered in app.main so it can reuse the existing
# admin-auth dependency without introducing a circular import.
