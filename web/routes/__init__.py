from fastapi import APIRouter

from .dashboard import router as dashboard_router
from .jobs import router as jobs_router
from .history import router as history_router
from .partials import router as partials_router

router = APIRouter()
router.include_router(dashboard_router)
router.include_router(jobs_router)
router.include_router(history_router)
router.include_router(partials_router)
