from typing import Any

from fastapi import APIRouter

from app.lab_models import CHALLENGES

router = APIRouter(prefix="/api/lab", tags=["learning lab"])


@router.get("/challenges")
async def list_challenges() -> list[dict[str, Any]]:
    """Return the read-only field guide used by the scrolling demonstrations."""
    return CHALLENGES
