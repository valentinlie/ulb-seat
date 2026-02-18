from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from config import LIBRARIES

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def ctx(request: Request, **kwargs) -> dict:
    return {"request": request, "libraries": LIBRARIES, **kwargs}
