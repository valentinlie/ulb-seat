"""FastAPI application with APScheduler lifespan."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from core import scheduler as sched
from web.routes import router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    sched.start()
    yield
    sched.shutdown()


app = FastAPI(title="ULB Seat Reservation", lifespan=lifespan)
app.include_router(router)
