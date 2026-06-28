"""JWT auth handler with token caching for TRIP API."""
import logging
import os
import time
import httpx

logger = logging.getLogger("trip.mcp.auth")

_token = None
_token_expires = 0.0


def get_api_url():
    return os.environ.get("TRIP_API_URL", "http://localhost:8080")


def _log_api_response(method: str, path: str, response: httpx.Response, data=None) -> None:
    if response.is_error:
        logger.error(
            "API %s %s -> %s: %s",
            method,
            path,
            response.status_code,
            _api_error_message(response, method, path, data),
        )
    else:
        logger.info("API %s %s -> %s", method, path, response.status_code)


async def get_token():
    global _token, _token_expires
    if _token and time.time() < _token_expires:
        logger.debug("using cached JWT")
        return _token

    api_token = os.environ.get("TRIP_API_TOKEN", "")
    if api_token:
        logger.info(
            "exchanging API token for JWT at %s (token length=%d)",
            get_api_url(),
            len(api_token),
        )
        async with httpx.AsyncClient(base_url=get_api_url()) as client:
            r = await client.post("/api/by_token/login", headers={"X-Api-Token": api_token})
            _log_api_response("POST", "/api/by_token/login", r)
            r.raise_for_status()
            data = r.json()
            _token = data["access_token"]
            _token_expires = time.time() + 25 * 60
            logger.info("JWT obtained via API token (cached ~25 min)")
            return _token

    username = os.environ.get("TRIP_USERNAME", "")
    password = os.environ.get("TRIP_PASSWORD", "")
    if not username or not password:
        logger.error("no auth configured: set TRIP_API_TOKEN or TRIP_USERNAME/TRIP_PASSWORD")
        raise RuntimeError("TRIP_API_TOKEN or TRIP_USERNAME and TRIP_PASSWORD env vars required")
    logger.info("logging in with username/password at %s (user=%s)", get_api_url(), username)
    async with httpx.AsyncClient(base_url=get_api_url()) as client:
        r = await client.post("/api/auth/login", json={"username": username, "password": password})
        _log_api_response("POST", "/api/auth/login", r)
        r.raise_for_status()
        data = r.json()
        _token = data["access_token"]
        _token_expires = time.time() + 25 * 60
        logger.info("JWT obtained via username/password (cached ~25 min)")
        return _token


async def api_get(path, params=None):
    token = await get_token()
    logger.debug("API GET %s params=%s", path, params)
    async with httpx.AsyncClient(base_url=get_api_url(), timeout=30) as client:
        r = await client.get(path, params=params, headers={"Authorization": f"Bearer {token}"})
        _log_api_response("GET", path, r)
        if r.status_code == 404:
            return {}
        r.raise_for_status()
        return r.json()


def _api_error_message(response: httpx.Response, method: str, path: str, data=None) -> str:
    try:
        detail = response.json().get("detail", response.text)
    except Exception:
        detail = response.text
    message = f"TRIP API {response.status_code} on {method} {path}: {detail}"
    if data is not None:
        message += f" | payload={data}"
    return message


async def api_post(path, data):
    token = await get_token()
    logger.info("API POST %s payload=%s", path, data)
    async with httpx.AsyncClient(base_url=get_api_url(), timeout=30) as client:
        r = await client.post(path, json=data, headers={"Authorization": f"Bearer {token}"})
        _log_api_response("POST", path, r, data)
        if r.is_error:
            raise RuntimeError(_api_error_message(r, "POST", path, data))
        return r.json()


async def api_put(path, data):
    token = await get_token()
    logger.info("API PUT %s payload=%s", path, data)
    async with httpx.AsyncClient(base_url=get_api_url(), timeout=30) as client:
        r = await client.put(path, json=data, headers={"Authorization": f"Bearer {token}"})
        _log_api_response("PUT", path, r, data)
        if r.is_error:
            raise RuntimeError(_api_error_message(r, "PUT", path, data))
        return r.json()


async def api_delete(path):
    token = await get_token()
    logger.info("API DELETE %s", path)
    async with httpx.AsyncClient(base_url=get_api_url(), timeout=30) as client:
        r = await client.delete(path, headers={"Authorization": f"Bearer {token}"})
        _log_api_response("DELETE", path, r)
        r.raise_for_status()
        return {"deleted": True}