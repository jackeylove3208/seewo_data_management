from collections.abc import AsyncIterator
from pathlib import Path

import anyio
import httpcore
import httpx
import pytest

from app.remote_sources.network import (
    PinnedNetworkBackend,
    RemoteCsvDownloader,
    RemoteSourceFailure,
    require_public_address,
)


class FakeResolver:
    def __init__(self, answers: dict[str, tuple[str, ...]]) -> None:
        self.answers = answers
        self.requests: list[tuple[str, int]] = []

    async def resolve(self, host: str, port: int) -> tuple[str, ...]:
        self.requests.append((host, port))
        return self.answers.get(host, ())


class ChunkedStream(httpx.AsyncByteStream):
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self.chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk


class RecordingNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self) -> None:
        self.hosts: list[str] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109
        local_address: str | None = None,
        socket_options=None,
    ):
        del port, timeout, local_address, socket_options
        self.hosts.append(host)
        return httpcore.AsyncMockStream([])


@pytest.mark.parametrize(
    "value",
    (
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "224.0.0.1",
        "240.0.0.1",
        "::1",
        "fe80::1",
        "ff02::1",
    ),
)
def test_rejects_non_global_destination(value: str) -> None:
    with pytest.raises(RemoteSourceFailure) as raised:
        require_public_address(value)

    assert raised.value.code == "remote_source_dns_rejected"


@pytest.mark.asyncio
async def test_pinned_backend_connects_to_the_preapproved_address() -> None:
    resolver = FakeResolver({"data.example.test": ("8.8.8.8",)})
    network = RecordingNetworkBackend()
    backend = PinnedNetworkBackend(resolver, backend=network)

    await backend.approve("data.example.test", 443)
    resolver.answers["data.example.test"] = ("10.0.0.1",)
    await backend.connect_tcp("data.example.test", 443)

    assert network.hosts == ["8.8.8.8"]
    assert resolver.requests == [("data.example.test", 443)]


def _downloader(
    handler: httpx.AsyncBaseTransport | httpx.MockTransport,
    *,
    resolver: FakeResolver | None = None,
    max_redirects: int = 3,
    max_bytes: int = 1024,
    total_timeout: float = 1,
) -> RemoteCsvDownloader:
    return RemoteCsvDownloader(
        resolver=resolver or FakeResolver({"data.example.test": ("8.8.8.8",)}),
        client=httpx.AsyncClient(transport=handler, follow_redirects=False),
        max_redirects=max_redirects,
        max_bytes=max_bytes,
        connect_timeout=0.5,
        read_timeout=0.5,
        total_timeout=total_timeout,
    )


@pytest.mark.asyncio
async def test_downloads_and_hashes_a_bounded_csv(tmp_path: Path) -> None:
    body = "编号,姓名\nS001,张三\n".encode()
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            200,
            headers={"content-type": "text/csv; charset=utf-8"},
            content=body,
        )
    )
    destination = tmp_path / "remote.csv"
    downloader = _downloader(transport)

    downloaded = await downloader.download(
        "https://data.example.test/roster.csv",
        destination,
    )

    assert downloaded.path == destination
    assert downloaded.size_bytes == len(body)
    assert downloaded.media_type == "text/csv"
    assert len(downloaded.content_sha256) == 64
    assert destination.read_bytes() == body


@pytest.mark.asyncio
async def test_rejects_mixed_public_and_private_dns_answers(tmp_path: Path) -> None:
    resolver = FakeResolver(
        {"data.example.test": ("8.8.8.8", "169.254.169.254")}
    )
    downloader = _downloader(
        httpx.MockTransport(lambda _request: httpx.Response(200, content=b"a,b\n1,2\n")),
        resolver=resolver,
    )

    with pytest.raises(RemoteSourceFailure) as raised:
        await downloader.download(
            "https://data.example.test/roster.csv",
            tmp_path / "remote.csv",
        )

    assert raised.value.code == "remote_source_dns_rejected"


@pytest.mark.asyncio
async def test_redirect_destination_is_revalidated(tmp_path: Path) -> None:
    resolver = FakeResolver(
        {
            "public.example.test": ("8.8.8.8",),
            "private.example.test": ("10.0.0.1",),
        }
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "public.example.test":
            return httpx.Response(
                302,
                headers={"location": "https://private.example.test/roster.csv"},
            )
        return httpx.Response(200, content=b"a,b\n1,2\n")

    downloader = _downloader(httpx.MockTransport(handler), resolver=resolver)

    with pytest.raises(RemoteSourceFailure) as raised:
        await downloader.download(
            "https://public.example.test/roster.csv",
            tmp_path / "remote.csv",
        )

    assert raised.value.code == "remote_source_dns_rejected"
    assert resolver.requests == [
        ("public.example.test", 443),
        ("private.example.test", 443),
    ]


@pytest.mark.asyncio
async def test_rejects_https_redirect_downgrade(tmp_path: Path) -> None:
    downloader = _downloader(
        httpx.MockTransport(
            lambda _request: httpx.Response(
                302,
                headers={"location": "http://data.example.test/roster.csv"},
            )
        )
    )

    with pytest.raises(RemoteSourceFailure) as raised:
        await downloader.download(
            "https://data.example.test/start.csv",
            tmp_path / "remote.csv",
        )

    assert raised.value.code == "remote_source_redirect_rejected"


@pytest.mark.asyncio
async def test_rejects_ip_literal_before_transport(tmp_path: Path) -> None:
    resolver = FakeResolver({})
    downloader = _downloader(
        httpx.MockTransport(lambda _request: httpx.Response(200, content=b"a,b\n1,2\n")),
        resolver=resolver,
    )

    with pytest.raises(RemoteSourceFailure) as raised:
        await downloader.download(
            "https://8.8.8.8/roster.csv",
            tmp_path / "remote.csv",
        )

    assert raised.value.code == "remote_source_url_rejected"
    assert resolver.requests == []


@pytest.mark.asyncio
async def test_rejects_redirect_above_limit(tmp_path: Path) -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            302,
            headers={"location": f"https://data.example.test{request.url.path}x"},
        )
    )
    downloader = _downloader(transport, max_redirects=3)

    with pytest.raises(RemoteSourceFailure) as raised:
        await downloader.download(
            "https://data.example.test/a.csv",
            tmp_path / "remote.csv",
        )

    assert raised.value.code == "remote_source_redirect_limit"


@pytest.mark.asyncio
async def test_rejects_declared_content_length_above_limit(tmp_path: Path) -> None:
    downloader = _downloader(
        httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={"content-length": "2048", "content-type": "text/csv"},
                content=b"a,b\n1,2\n",
            )
        ),
        max_bytes=1024,
    )

    with pytest.raises(RemoteSourceFailure) as raised:
        await downloader.download(
            "https://data.example.test/a.csv",
            tmp_path / "remote.csv",
        )

    assert raised.value.code == "remote_source_too_large"
    assert not (tmp_path / "remote.csv").exists()


@pytest.mark.asyncio
async def test_rejects_stream_above_limit_and_removes_partial_file(tmp_path: Path) -> None:
    downloader = _downloader(
        httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={"content-type": "text/csv"},
                stream=ChunkedStream((b"a,b\n", b"1" * 1024)),
            )
        ),
        max_bytes=16,
    )
    destination = tmp_path / "remote.csv"

    with pytest.raises(RemoteSourceFailure) as raised:
        await downloader.download("https://data.example.test/a.csv", destination)

    assert raised.value.code == "remote_source_too_large"
    assert not destination.exists()


@pytest.mark.parametrize(
    ("content_type", "body"),
    (
        ("text/html", b"<html><body>login</body></html>"),
        ("application/json", b'{"rows": []}'),
        ("application/zip", b"PK\x03\x04archive"),
        (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            b"PK\x03\x04workbook",
        ),
    ),
)
@pytest.mark.asyncio
async def test_rejects_non_csv_content(
    tmp_path: Path,
    content_type: str,
    body: bytes,
) -> None:
    downloader = _downloader(
        httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={"content-type": content_type},
                content=body,
            )
        )
    )

    with pytest.raises(RemoteSourceFailure) as raised:
        await downloader.download(
            "https://data.example.test/a.csv",
            tmp_path / "remote.csv",
        )

    assert raised.value.code == "remote_source_content_rejected"


@pytest.mark.parametrize("body", (b"", b"a,b\n1\n"))
@pytest.mark.asyncio
async def test_rejects_empty_or_malformed_csv(tmp_path: Path, body: bytes) -> None:
    downloader = _downloader(
        httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                headers={"content-type": "text/csv"},
                content=body,
            )
        )
    )

    with pytest.raises(RemoteSourceFailure) as raised:
        await downloader.download(
            "https://data.example.test/a.csv",
            tmp_path / "remote.csv",
        )

    assert raised.value.code == "remote_source_csv_invalid"


@pytest.mark.asyncio
async def test_classifies_transport_timeout_without_url_leak(tmp_path: Path) -> None:
    submitted_url = "https://data.example.test/a.csv?secret=value"

    def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("request failed " + submitted_url)

    downloader = _downloader(httpx.MockTransport(timeout))

    with pytest.raises(RemoteSourceFailure) as raised:
        await downloader.download(submitted_url, tmp_path / "remote.csv")

    assert raised.value.code == "remote_source_timeout"
    assert submitted_url not in str(raised.value)
    assert "secret=value" not in str(raised.value)
    assert raised.value.__cause__ is None


@pytest.mark.asyncio
async def test_enforces_total_timeout(tmp_path: Path) -> None:
    async def slow(_request: httpx.Request) -> httpx.Response:
        await anyio.sleep(0.05)
        return httpx.Response(200, content=b"a,b\n1,2\n")

    downloader = _downloader(
        httpx.MockTransport(slow),
        total_timeout=0.01,
    )

    with pytest.raises(RemoteSourceFailure) as raised:
        await downloader.download(
            "https://data.example.test/a.csv",
            tmp_path / "remote.csv",
        )

    assert raised.value.code == "remote_source_timeout"
