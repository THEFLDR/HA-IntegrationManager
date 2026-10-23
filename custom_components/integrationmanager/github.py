"""Bounded, cached public HTTPS reads. No authentication or GitHub writes."""

import asyncio
import hashlib
import json
import time
from urllib.parse import quote, urlparse

import aiohttp

from .catalog import parse_catalog, validate_repository
from .const import CATALOG_URL, MAX_ARCHIVE


class GitHubError(Exception):
    """A safe user-facing remote service failure."""

    def __init__(self, message, *, transient=False):
        super().__init__(message)
        self.transient = transient


class GitHubClient:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.cache = {}

    def invalidate_releases(self, repository):
        """Allow an explicit update check to bypass only that repository's cache."""
        validate_repository(repository)
        prefix = f"https://api.github.com/repos/{repository}/releases?"
        for url in list(self.cache):
            if url.startswith(prefix):
                del self.cache[url]

    async def _read(self, url: str, limit: int, accept: str) -> bytes:
        try:
            async with asyncio.timeout(30):
                for _ in range(5):
                    async with self.session.get(
                        url,
                        headers={"Accept": accept},
                        allow_redirects=False,
                    ) as response:
                        if response.status in (301, 302, 303, 307, 308):
                            url = response.headers.get("Location", "")
                            parsed = urlparse(url)
                            if (
                                parsed.scheme != "https"
                                or parsed.hostname
                                not in {"github.com", "release-assets.githubusercontent.com"}
                                or parsed.username
                                or parsed.password
                                or parsed.port not in (None, 443)
                            ):
                                raise GitHubError("Unsafe download redirect")
                            continue
                        if response.status != 200:
                            raise GitHubError(
                                f"Public GitHub request failed (HTTP {response.status}); "
                                "try again later (rate limits may apply)",
                                transient=response.status in {403, 429} or response.status >= 500,
                            )
                        result = bytearray()
                        async for chunk in response.content.iter_chunked(65536):
                            result.extend(chunk)
                            if len(result) > limit:
                                raise GitHubError("Remote response exceeds size limit")
                        return bytes(result)
                raise GitHubError("Too many download redirects")
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise GitHubError(
                "Public GitHub request failed; try again later", transient=True
            ) from err

    async def _json(self, url: str):
        cached = self.cache.get(url)
        if cached and time.monotonic() - cached[0] < 300:
            return cached[1]
        raw = await self._read(url, 2 * 1024 * 1024, "application/vnd.github+json")
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError) as err:
            raise GitHubError("Invalid public JSON response") from err
        self.cache[url] = (time.monotonic(), payload)
        return payload

    async def catalog(self):
        try:
            return parse_catalog(await self._json(CATALOG_URL))
        except ValueError as err:
            raise GitHubError(str(err)) from err

    async def releases(self, repository: str) -> list:
        validate_repository(repository)
        result = []
        # Bounded pagination avoids losing a Stable release behind many RCs.
        for page in range(1, 6):
            payload = await self._json(
                f"https://api.github.com/repos/{repository}/releases?per_page=100&page={page}"
            )
            if not isinstance(payload, list) or any(not isinstance(i, dict) for i in payload):
                raise GitHubError("Invalid release list")
            result.extend(payload)
            if len(payload) < 100:
                return result
        raise GitHubError("Release history exceeds supported bound; cannot safely select a release")

    async def download(self, repository, domain, release):
        validate_repository(repository)
        # GitHub immutable releases guarantee this tagged asset cannot be replaced.
        url = (
            f"https://github.com/{repository}/releases/download/"
            f"{quote(release.tag, safe='')}/{domain}.zip"
        )
        data = await self._read(url, MAX_ARCHIVE, "application/octet-stream")
        if len(data) != release.size or hashlib.sha256(data).hexdigest() != release.digest:
            raise GitHubError("Release artifact checksum or size mismatch")
        return data
