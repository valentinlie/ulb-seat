"""HTTP Basic Auth for the web dashboard."""

import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from config import DASHBOARD_USER, DASHBOARD_PASS

security = HTTPBasic()


def require_auth(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    """Verify HTTP Basic Auth credentials. Returns the username."""
    correct_user = secrets.compare_digest(credentials.username.encode(), DASHBOARD_USER.encode())
    correct_pass = secrets.compare_digest(credentials.password.encode(), DASHBOARD_PASS.encode())
    if not (correct_user and correct_pass):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username
