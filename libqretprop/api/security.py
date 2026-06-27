from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials


security = HTTPBasic()

# Hardcoded creds
ALLOWED_USERS = {
    "noah": "stinkylion",
    "admin": "propteambestteam",
}


def auth_user(creds: Annotated[HTTPBasicCredentials, Depends(security)]) -> str:
    valid_creds = creds.username in ALLOWED_USERS and creds.password == ALLOWED_USERS[creds.username]

    if not valid_creds:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
            headers={"WWW-Authenticate": "Basic"},
        )
    return creds.username

