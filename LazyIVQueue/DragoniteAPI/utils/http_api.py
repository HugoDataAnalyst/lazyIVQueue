import aiohttp
from typing import Any, Dict, Optional, Tuple
from yarl import URL
from LazyIVQueue.utils.logger import logger

def _normalize_base_url(base_url: str) -> str:
    base_url = base_url.strip()
    if not base_url:
        raise ValueError("Base URL is empty")
    if not base_url.startswith(("http://", "https://")):
        base_url = "http://" + base_url
    return base_url.rstrip("/")

class APIClient:
    """
    Minimal aiohttp client with optional auth:
      - username/password  -> Basic Auth
      - bearer             -> Authorization: Bearer <token>
      - secret             -> X-API-Key: <secret>   (adjust header name if your API differs)
    """
    def __init__(
        self,
        base_url: str,
        *,
        username: Optional[str] = None,
        password: Optional[str] = None,
        bearer: Optional[str] = None,
        secret: Optional[str] = None,
        timeout_s: int = 20,
        headers: Optional[Dict[str, str]] = None,
    ) -> None:
        self.base_url = _normalize_base_url(base_url)
        self.username = username
        self.password = password
        self.bearer = bearer
        self.secret = secret
        self.timeout = aiohttp.ClientTimeout(total=timeout_s)
        self._session: Optional[aiohttp.ClientSession] = None
        self._extra_headers = headers or {}

    def _build_headers(self) -> Dict[str, str]:
        h = {"Accept": "application/json", **self._extra_headers}
        if self.bearer:
            h["Authorization"] = f"Bearer {self.bearer}"
        if self.secret:
            h["X-API-Key"] = self.secret
        # Basic is set on the request via auth= below, not as a header string
        return h

    def _url(self, path: str) -> str:
        path = path if path.startswith("/") else f"/{path}"
        return str(URL(self.base_url) / path.lstrip("/"))

    async def __aenter__(self):
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self.timeout, raise_for_status=True)
        return self

    async def __aexit__(self, *exc):
        if self._session:
            await self._session.close()
            self._session = None

    @property
    def session(self) -> aiohttp.ClientSession:
        if not self._session:
            raise RuntimeError("APIClient not started. Use `async with APIClient(...)` or call __aenter__.")
        return self._session

    def _basic_auth(self) -> Optional[aiohttp.BasicAuth]:
        if self.username is not None and self.password is not None:
            return aiohttp.BasicAuth(self.username, self.password)
        return None

    async def get(self, path: str, **params) -> Any:
        async with self.session.get(
            self._url(path),
            params=params or None,
            headers=self._build_headers(),
            auth=self._basic_auth(),
        ) as r:
            # If response might be text sometimes, adjust here.
            return await r.json(content_type=None)

    async def get_text(self, path: str, **params) -> str:
        """
        GET a plain-text endpoint (e.g. Prometheus /metrics).
        Uses text Accept header and returns the raw body as str.
        """
        # start from your normal headers but override Accept for text
        headers = dict(self._build_headers())
        headers["Accept"] = "text/plain; charset=utf-8"

        async with self.session.get(
            self._url(path),
            params=params or None,
            headers=headers,
            auth=self._basic_auth(),
        ) as r:
            return await r.text()

    async def get_json(self, path: str, **params) -> Any:
        """
        GET a JSON endpoint (e.g. /api/status).
        Uses application/json Accept header and returns the raw body as dict.
        """
        async with self.session.get(
            self._url(path),
            params=params or None,
            headers=self._build_headers(),
            auth=self._basic_auth(),
        ) as r:
            return await r.json(content_type=None)

    async def post(self, path: str, json: Any = None) -> Any:
        async with self.session.post(
            self._url(path),
            json=json,
            headers=self._build_headers(),
            auth=self._basic_auth(),
        ) as r:
            return await r.json(content_type=None)

    async def post_json(self, path: str, json: Any = None) -> Any:
        """
        POST a JSON body and parse JSON response (lenient content_type).
        """
        async with self.session.post(
            self._url(path),
            json=json,
            headers=self._build_headers(),
            auth=self._basic_auth(),
        ) as r:
            return await r.json(content_type=None)

    async def post_bytes(
        self,
        path: str,
        data: Any = None,
        extra_headers: Optional[Dict[str, str]] = None
    ) -> Tuple[bytes, Dict[str, str], int]:
        """
        POST with no JSON semantics. Returns (body_bytes, response_headers, status).
        - Sends NO Content-Type header (matches bare fetch()).
        - Accept */* so binary responses are fine.
        """
        headers = dict(self._build_headers())

        # Rotom returns application/zip
        headers["Accept"] = "*/*"

        # Ensure NO Content-Type header is present
        headers.pop("Content-Type", None)

        if extra_headers:
            headers.update(extra_headers)
            # Just in case the caller tried to add a Content-Type again
            headers.pop("Content-Type", None)

        async with self.session.post(
            self._url(path),
            data=data,                       # keep this None for bare POST
            headers=headers,
            auth=self._basic_auth(),
            # <—— prevent aiohttp from auto-inserting Content-Type
            skip_auto_headers={"Content-Type"},
        ) as r:
            body = await r.read()
            return body, dict(r.headers), r.status


    async def patch(self, path: str, json: Any = None) -> Any:
        async with self.session.patch(
            self._url(path),
            json=json,
            headers=self._build_headers(),
            auth=self._basic_auth(),
        ) as r:
            return await r.json(content_type=None)

    async def delete(self, path: str) -> Any:
        async with self.session.delete(
            self._url(path),
            headers=self._build_headers(),
            auth=self._basic_auth(),
        ) as r:
            # Some DELETEs return empty bodies; tolerate that
            try:
                return await r.json(content_type=None)
            except Exception:
                return {"status": r.status}
