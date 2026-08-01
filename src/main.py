"""Manual/dev entry point: `uv run src/main.py`.

For the on-demand, socket-activated deployment use the systemd units instead
(`ulb install` / `ulb enable`); those run `cli.py web --fd 3`. Launched by
hand we disable the idle-shutdown watchdog so the server stays up.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("ULB_WEB_IDLE_TIMEOUT", "0")

# config.py lives in the repo root, outside this import root (see cli.py).
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "src"))

import uvicorn

from config import HOST, PORT


def main() -> None:
    # no --reload: a reloader would run two copies of the app
    uvicorn.run("web.app:app", host=HOST, port=PORT)


if __name__ == "__main__":
    main()
