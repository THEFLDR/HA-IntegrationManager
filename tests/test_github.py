import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import aiohttp
import pytest

from custom_components.integrationmanager.github import GitHubClient, GitHubError
from custom_components.integrationmanager.releases import Release


class Response:
    def __init__(self, data=b"[]", status=200, location=None):
        self.status = status
        self.headers = {"Location": location} if location else {}
        self.content = self
        self.data = data

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def iter_chunked(self, size):
        for start in range(0, len(self.data), size):
            yield self.data[start : start + size]


def client(*responses):
    return GitHubClient(SimpleNamespace(get=Mock(side_effect=responses)))


async def test_cache_and_timeout():
    github = client(Response())
    assert await github.catalog() == []
    assert await github.catalog() == []
    assert github.session.get.call_count == 1
    github.cache.clear()
    github.session.get.side_effect = TimeoutError()
    with pytest.raises(GitHubError):
        await github.catalog()


@pytest.mark.parametrize("status", [403, 404, 429, 500])
async def test_http_errors(status):
    with pytest.raises(GitHubError, match=str(status)):
        await client(Response(status=status)).catalog()


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/file",
        "https://evil.example/file",
        "https://user@github.com/file",
        "https://github.com:8443/file",
        "file:///etc/passwd",
        "/relative",
    ],
)
async def test_reject_redirects(url):
    with pytest.raises(GitHubError, match="redirect"):
        await client(Response(status=302, location=url)).catalog()


async def test_safe_redirect_and_size_bound():
    github = client(
        Response(status=302, location="https://release-assets.githubusercontent.com/asset"),
        Response(b"hello"),
    )
    assert await github._read("https://github.com/asset", 5, "application/octet-stream") == b"hello"
    with pytest.raises(GitHubError, match="size"):
        await client(Response(b"longer"))._read(
            "https://github.com/asset", 2, "application/octet-stream"
        )


async def test_invalid_json_schema_and_redirect_loop():
    for value in [b"invalid", b"{}", b"[{}]"]:
        with pytest.raises(GitHubError):
            await client(Response(value)).catalog()
    github = client(*[Response(status=302, location="https://github.com/loop") for _ in range(5)])
    with pytest.raises(GitHubError, match="Too many"):
        await github.catalog()


async def test_pagination():
    github = client(Response(b"[{}]"))
    assert await github.releases("THEFLDR/Repo") == [{}]
    github._json = AsyncMock(side_effect=[[{}] * 100, [{}]])
    assert len(await github.releases("THEFLDR/Repo")) == 101
    github._json = AsyncMock(return_value=[{}] * 100)
    with pytest.raises(GitHubError, match="bound"):
        await github.releases("THEFLDR/Repo")
    github._json = AsyncMock(return_value={})
    with pytest.raises(GitHubError, match="list"):
        await github.releases("THEFLDR/Repo")


async def test_download_digest_and_immutable_url():
    data = b"artifact"
    release = Release(
        "0.2.0",
        "0.2.0",
        42,
        hashlib.sha256(data).hexdigest(),
        len(data),
        "https://evil.example",
        "",
    )
    github = client(Response(data))
    assert await github.download("THEFLDR/Repo", "sample", release) == data
    assert (
        github.session.get.call_args.args[0]
        == "https://github.com/THEFLDR/Repo/releases/download/0.2.0/sample.zip"
    )
    with pytest.raises(GitHubError, match="checksum"):
        await client(Response(b"corrupt")).download("THEFLDR/Repo", "sample", release)


async def test_cancellation_propagates():
    github = client()
    github.session.get.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await github.catalog()


async def test_connection_failure():
    github = client()
    github.session.get.side_effect = aiohttp.ClientError("offline")
    with pytest.raises(GitHubError):
        await github.catalog()
