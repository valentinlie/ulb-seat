"""Entry point: `uv run src/main.py`."""

import uvicorn

from config import HOST, PORT


def main() -> None:
    # no --reload: the reloader would start the APScheduler twice
    uvicorn.run("web.app:app", host=HOST, port=PORT)


if __name__ == "__main__":
    main()
