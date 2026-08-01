from fastapi import APIRouter

from .auth import router as auth_router
from .accounts import router as accounts_router
from .admin import router as admin_router
from .dashboard import router as dashboard_router
from .jobs import router as jobs_router
from .history import router as history_router

router = APIRouter()
router.include_router(auth_router)
router.include_router(accounts_router)
router.include_router(admin_router)
router.include_router(dashboard_router)
router.include_router(jobs_router)
router.include_router(history_router)
