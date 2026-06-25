"""
Polygon REST API client with rate limiting.

Wraps httpx async HTTP client; auto-signs every request
using the HMAC-SHA512 auth module.
"""

import asyncio

import httpx

from modules.polygon.auth import sign_request

POLYGON_API_URL = "https://polygon.codeforces.com/api/"
REQUEST_DELAY = 0.5  # seconds between requests (rate limit)


class PolygonAPIError(Exception):
    """Raised when a Polygon API call returns status FAILED."""

    def __init__(self, method: str, comment: str):
        self.method = method
        self.comment = comment
        super().__init__(f"Polygon API [{method}]: {comment}")


class PolygonClient:
    """
    Async client for the Polygon REST API.

    Usage:
        client = PolygonClient(api_key, secret)
        result = await client.call("problem.create", name="my-problem")
        await client.close()
    """

    def __init__(self, api_key: str, secret: str):
        self.api_key = api_key
        self.secret = secret
        self._client = httpx.AsyncClient(timeout=60.0)
        self._last_request_time: float = 0

    async def call(self, method_name: str, **params) -> dict:
        """
        Call a Polygon API method.

        Args:
            method_name: e.g. "problem.create", "problem.saveStatement"
            **params: Method-specific parameters.

        Returns:
            Parsed JSON response dict.

        Raises:
            PolygonAPIError: If the API returns status "FAILED".
        """
        # Rate limiting
        loop = asyncio.get_event_loop()
        now = loop.time()
        elapsed = now - self._last_request_time
        if elapsed < REQUEST_DELAY:
            await asyncio.sleep(REQUEST_DELAY - elapsed)

        # Convert all param values to strings (Polygon expects form data)
        str_params = {}
        for k, v in params.items():
            if isinstance(v, bool):
                str_params[k] = "true" if v else "false"
            else:
                str_params[k] = str(v)

        signed = sign_request(method_name, str_params, self.api_key, self.secret)
        url = f"{POLYGON_API_URL}{method_name}"

        response = await self._client.post(url, data=signed)
        self._last_request_time = asyncio.get_event_loop().time()

        # Some methods return raw content (not JSON)
        content_type = response.headers.get("content-type", "")
        if "application/json" not in content_type:
            if response.status_code == 200:
                return {"status": "OK", "result": response.text}
            else:
                raise PolygonAPIError(method_name, f"HTTP {response.status_code}: {response.text[:200]}")

        # Handle empty JSON body
        if not response.text.strip():
            if response.status_code == 200:
                return {"status": "OK", "result": None}
            else:
                raise PolygonAPIError(method_name, f"HTTP {response.status_code}: empty response")

        data = response.json()
        import logging as _logging; _logging.getLogger("polygon-uploader.client").debug("API %s response: %s", method_name, str(data)[:500])
        if data.get("status") == "FAILED":
            raise PolygonAPIError(
                method_name,
                data.get("comment", "Unknown error"),
            )

        return data

    async def close(self):
        """Close the underlying HTTP client."""
        await self._client.aclose()