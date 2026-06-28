from datetime import UTC, datetime, timedelta
from ipaddress import ip_address, ip_network

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2 import exceptions as argon_exceptions
from authlib.integrations.httpx_client import OAuth2Client
from fastapi import HTTPException, Request
from sqlmodel import Session, select

from .config import get_settings
from .models.models import Token, User
from .utils.utils import httpx_get

ph = PasswordHasher()
OIDC_CONFIG = {}
DEFAULT_API_TOKEN_CIDRS = ("127.0.0.0/8", "::1/128", "172.16.0.0/12", "10.0.0.0/8")


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def verify_totp_code(secret: str, code: str) -> bool:
    totp = pyotp.TOTP(secret)
    return totp.verify(code)


def hash_password(password: str) -> str:
    return ph.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    try:
        return ph.verify(hashed_password, plain_password)
    except (
        argon_exceptions.VerifyMismatchError,
        argon_exceptions.VerificationError,
        argon_exceptions.InvalidHashError,
    ):
        raise HTTPException(status_code=401, detail="Invalid credentials")


def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(minutes=get_settings().ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, get_settings().SECRET_KEY, algorithm=get_settings().ALGORITHM)


def create_refresh_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(UTC) + timedelta(minutes=get_settings().REFRESH_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, get_settings().SECRET_KEY, algorithm=get_settings().ALGORITHM)


def create_tokens(data: dict) -> Token:
    return Token(access_token=create_access_token(data), refresh_token=create_refresh_token(data))


def verify_exists_and_owns(username: str, obj) -> None:
    if not obj:
        raise HTTPException(status_code=404, detail="The resource does not exist")

    if obj.user != username:
        raise HTTPException(status_code=403, detail="Forbidden")

    return None


def verify_api_token_client(request: Request) -> None:
    if not get_settings().API_TOKEN_LOCAL_ONLY:
        return

    client_host = request.client.host if request.client else None
    if not client_host:
        raise HTTPException(status_code=403, detail="Forbidden")

    allowed_cidrs = get_settings().API_TOKEN_ALLOWED_CIDRS
    if allowed_cidrs:
        networks = [ip_network(cidr.strip()) for cidr in allowed_cidrs.split(",") if cidr.strip()]
    else:
        networks = [ip_network(cidr) for cidr in DEFAULT_API_TOKEN_CIDRS]

    try:
        addr = ip_address(client_host)
    except ValueError:
        raise HTTPException(status_code=403, detail="Forbidden")

    if not any(addr in network for network in networks):
        raise HTTPException(status_code=403, detail="Forbidden")


def api_token_to_user(session: Session, api_token: str) -> User | None:
    if not api_token:
        raise HTTPException(status_code=400, detail="Bad Request")

    user = session.exec(select(User).where(User.api_token == api_token)).first()
    if not user:
        raise HTTPException(status_code=401, detail="Invalid Token")
    return user


def get_oidc_client():
    return OAuth2Client(
        client_id=get_settings().OIDC_CLIENT_ID,
        client_secret=get_settings().OIDC_CLIENT_SECRET,
        scope="openid profile",
        redirect_uri=get_settings().OIDC_REDIRECT_URI,
    )


async def get_oidc_config():
    global OIDC_CONFIG
    if OIDC_CONFIG:
        return OIDC_CONFIG

    discovery_url = get_settings().OIDC_DISCOVERY_URL
    if not discovery_url:
        raise HTTPException(status_code=500, detail="OIDC_DISCOVERY_URL not configured")

    OIDC_CONFIG = await httpx_get(discovery_url)
    return OIDC_CONFIG
