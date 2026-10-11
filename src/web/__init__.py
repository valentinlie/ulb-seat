from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from config import LIBRARIES
from core.booking import DAYS_AHEAD, OPENS_AT, POINTS_PER_WEEK, opens_at
from core.reservation import CATEGORIES

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

OPENING = f"{OPENS_AT:%H:%M}, {DAYS_AHEAD} days ahead"


def ctx(request: Request, auth=None, **kwargs) -> dict:
    """Template context. ``auth`` drives the nav bar and the account switcher."""
    return {"request": request, "libraries": LIBRARIES, "categories": CATEGORIES,
            "opening": OPENING, "opens_at": opens_at, "points_per_week": POINTS_PER_WEEK, "auth": auth, **kwargs}
