"""FastAPI application for the ULB seat dashboard.

Scheduling lives in systemd timers now (see ``core/systemd.py``), so this app
only serves the UI. It is socket-activated and shuts itself down once idle so
it does not sit resident between visits.
"""

import asyncio
import logging
import os
import signal
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from starlette.middleware.sessions import SessionMiddleware

from config import AUTH_ENABLED, SESSION_MAX_AGE, SESSION_SECRET
from core import db
from web.auth import NotAuthenticated, not_authenticated_handler
from web.routes import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

# Shut the socket-activated UI back down after this many idle seconds. 0 keeps
# it running (e.g. when started by hand for development).
IDLE_TIMEOUT = int(os.environ.get("ULB_WEB_IDLE_TIMEOUT", "600"))


async def _idle_watchdog(app: FastAPI) -> None:
    while True:
        await asyncio.sleep(30)
        idle = time.monotonic() - app.state.last_request
        if idle >= IDLE_TIMEOUT:
            log.info("Web UI idle for %.0fs, shutting down (socket stays active)", idle)
            os.kill(os.getpid(), signal.SIGTERM)
            return


def _warn_about_auth() -> None:
    if not AUTH_ENABLED:
        log.warning("AUTH_ENABLED is False — the dashboard is served with NO authentication. "
                    "Only do this when it is unreachable from the network.")
    elif SESSION_SECRET == "change_me":
        log.warning("SESSION_SECRET is still the placeholder from config.py — anyone who knows "
                    "it can forge a login cookie. Set it to a random value.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _warn_about_auth()
    db.init_db()
    app.state.last_request = time.monotonic()
    watchdog = asyncio.create_task(_idle_watchdog(app)) if IDLE_TIMEOUT > 0 else None
    yield
    if watchdog:
        watchdog.cancel()
        try:
            await watchdog
        except asyncio.CancelledError:
            pass
    db.close_pool()


app = FastAPI(title="ULB Seat Reservation", lifespan=lifespan)

# Holds the passkey challenge mid-ceremony and the logged-in flag afterwards.
# https_only is safe here: WebAuthn only works on a secure origin anyway.
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET,
    max_age=SESSION_MAX_AGE,
    same_site="lax",
    https_only=True,
)
app.add_exception_handler(NotAuthenticated, not_authenticated_handler)


@app.middleware("http")
async def _track_activity(request: Request, call_next):
    request.app.state.last_request = time.monotonic()
    return await call_next(request)


app.include_router(router)
