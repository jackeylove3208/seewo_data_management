import hashlib
import ipaddress
import socket
import ssl
from collections.abc import AsyncIterable, AsyncIterator, Iterable
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Protocol, Self
from urllib.parse import SplitResult, urljoin, urlsplit

import anyio
import httpcore
import httpx

from app.ingestion.csv_reader import CsvFormatError, inspect_csv, read_csv_frame

_REDIRECT_STATUSES = {301, 302, 303, 307, 308}
_REJECTED_MEDIA_TYPES = {
    "application/json",
    "application/zip",
    "application/x-zip-compressed",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "text/html",
}


class RemoteSourceFailure(ValueError):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class AddressResolver(Protocol):
    async def resolve(self, host: str, port: int) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class DownloadedRemoteCsv:
    path: Path
    content_sha256: str
    size_bytes: int
    media_type: str
    detected_encoding: str


class SystemAddressResolver:
    async def resolve(self, host: str, port: int) -> tuple[str, ...]:
        def lookup() -> tuple[str, ...]:
            answers = socket.getaddrinfo(
                host,
                port,
                type=socket.SOCK_STREAM,
                proto=socket.IPPROTO_TCP,
            )
            return tuple(
                dict.fromkeys(str(answer[4][0]) for answer in answers)
            )

        try:
            return await anyio.to_thread.run_sync(lookup)
        except OSError as error:
            raise RemoteSourceFailure(
                "remote_source_dns_failed",
                "第三方数据域名暂时无法解析。",
            ) from error


def require_public_address(value: str) -> str:
    try:
        address = ipaddress.ip_address(value)
    except ValueError as error:
        raise RemoteSourceFailure(
            "remote_source_dns_rejected",
            "第三方数据地址不符合公网访问策略。",
        ) from error
    if (
        not address.is_global
        or address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise RemoteSourceFailure(
            "remote_source_dns_rejected",
            "第三方数据地址不符合公网访问策略。",
        )
    return address.compressed


def require_public_addresses(values: Iterable[str]) -> tuple[str, ...]:
    addresses = tuple(require_public_address(value) for value in values)
    if not addresses:
        raise RemoteSourceFailure(
            "remote_source_dns_rejected",
            "第三方数据域名没有可用的公网地址。",
        )
    return tuple(dict.fromkeys(addresses))


class _NetworkPolicy:
    def __init__(self, resolver: AddressResolver) -> None:
        self._resolver = resolver

    async def approve(self, host: str, port: int) -> tuple[str, ...]:
        return require_public_addresses(await self._resolver.resolve(host, port))


class PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(
        self,
        resolver: AddressResolver,
        *,
        backend: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        self._policy = _NetworkPolicy(resolver)
        self._backend = backend or httpcore.AnyIOBackend()
        self._approved: dict[tuple[str, int], tuple[str, ...]] = {}

    async def approve(self, host: str, port: int) -> tuple[str, ...]:
        approved = await self._policy.approve(host, port)
        self._approved[(host.casefold(), port)] = approved
        return approved

    async def connect_tcp(  # noqa: ASYNC109 - httpcore interface requires this name.
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 - required by httpcore
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        approved = self._approved.get((host.casefold(), port))
        if approved is None:
            approved = await self.approve(host, port)
        last_error: Exception | None = None
        for address in approved:
            try:
                return await self._backend.connect_tcp(
                    address,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except httpcore.NetworkError as error:
                last_error = error
        if last_error is not None:
            raise last_error
        raise httpcore.ConnectError("approved remote address was unavailable")

    async def connect_unix_socket(  # noqa: ASYNC109 - httpcore interface requires this name.
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 - required by httpcore
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        del path, timeout, socket_options
        raise httpcore.UnsupportedProtocol("Unix sockets are disabled")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class _HttpCoreResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream: AsyncIterable[bytes]) -> None:
        self._stream = stream

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._stream:
            yield chunk

    async def aclose(self) -> None:
        close = getattr(self._stream, "aclose", None)
        if close is not None:
            await close()


class PinnedAsyncTransport(httpx.AsyncBaseTransport):
    def __init__(self, backend: PinnedNetworkBackend) -> None:
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            network_backend=backend,
            max_connections=10,
            max_keepalive_connections=5,
            http1=True,
            http2=False,
            retries=0,
        )

    async def __aenter__(self) -> Self:
        await self._pool.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None = None,
        exc_value: BaseException | None = None,
        traceback: TracebackType | None = None,
    ) -> None:
        await self._pool.__aexit__(exc_type, exc_value, traceback)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        core_response = await self._pool.handle_async_request(
            httpcore.Request(
                method=request.method,
                url=httpcore.URL(
                    scheme=request.url.raw_scheme,
                    host=request.url.raw_host,
                    port=request.url.port,
                    target=request.url.raw_path,
                ),
                headers=request.headers.raw,
                content=request.stream,
                extensions=request.extensions,
            )
        )
        if not isinstance(core_response.stream, AsyncIterable):
            raise httpcore.ProtocolError("async response returned a sync stream")
        return httpx.Response(
            status_code=core_response.status,
            headers=core_response.headers,
            stream=_HttpCoreResponseStream(core_response.stream),
            extensions=core_response.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()


class RemoteCsvDownloader:
    def __init__(
        self,
        *,
        resolver: AddressResolver | None = None,
        client: httpx.AsyncClient | None = None,
        max_redirects: int = 3,
        max_bytes: int,
        connect_timeout: float,
        read_timeout: float,
        total_timeout: float,
    ) -> None:
        resolved = resolver or SystemAddressResolver()
        self._resolver = resolved
        self._client = client
        self._max_redirects = max_redirects
        self._max_bytes = max_bytes
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._total_timeout = total_timeout

    async def download(self, url: str, destination: Path) -> DownloadedRemoteCsv:
        if self._client is None:
            backend = PinnedNetworkBackend(self._resolver)
            async with httpx.AsyncClient(
                transport=PinnedAsyncTransport(backend),
                follow_redirects=False,
                timeout=httpx.Timeout(
                    connect=self._connect_timeout,
                    read=self._read_timeout,
                    write=self._read_timeout,
                    pool=self._connect_timeout,
                ),
                trust_env=False,
            ) as client:
                return await self._bounded_download(
                    url,
                    destination,
                    client=client,
                    policy=backend,
                )
        return await self._bounded_download(
            url,
            destination,
            client=self._client,
            policy=_NetworkPolicy(self._resolver),
        )

    async def _bounded_download(
        self,
        url: str,
        destination: Path,
        *,
        client: httpx.AsyncClient,
        policy: _NetworkPolicy | PinnedNetworkBackend,
    ) -> DownloadedRemoteCsv:
        try:
            with anyio.fail_after(self._total_timeout):
                return await self._download(
                    url,
                    destination,
                    client=client,
                    policy=policy,
                )
        except RemoteSourceFailure:
            raise
        except (TimeoutError, httpx.TimeoutException, httpcore.TimeoutException):
            raise RemoteSourceFailure(
                "remote_source_timeout",
                "第三方数据请求超时，请稍后重试。",
            ) from None
        except (httpx.HTTPError, httpcore.NetworkError, OSError):
            raise RemoteSourceFailure(
                "remote_source_transport_failed",
                "第三方数据暂时无法访问，请稍后重试。",
            ) from None

    async def _download(
        self,
        url: str,
        destination: Path,
        *,
        client: httpx.AsyncClient,
        policy: _NetworkPolicy | PinnedNetworkBackend,
    ) -> DownloadedRemoteCsv:
        current_url = url
        redirects = 0
        while True:
            parsed = _validate_https_url(current_url, redirect=redirects > 0)
            host = parsed.hostname
            assert host is not None
            try:
                port = parsed.port or 443
            except ValueError as error:
                raise RemoteSourceFailure(
                    "remote_source_redirect_rejected",
                    "第三方数据重定向地址不合法。",
                ) from error
            await policy.approve(host.encode("idna").decode("ascii").casefold(), port)
            async with client.stream("GET", current_url) as response:
                if response.status_code in _REDIRECT_STATUSES:
                    if redirects >= self._max_redirects:
                        raise RemoteSourceFailure(
                            "remote_source_redirect_limit",
                            "第三方数据重定向次数过多。",
                        )
                    location = response.headers.get("location")
                    if not location:
                        raise RemoteSourceFailure(
                            "remote_source_redirect_rejected",
                            "第三方数据重定向地址不合法。",
                        )
                    current_url = urljoin(current_url, location)
                    redirects += 1
                    continue
                if response.status_code < 200 or response.status_code >= 300:
                    raise RemoteSourceFailure(
                        "remote_source_http_status",
                        "第三方数据服务暂时未返回可用文件。",
                    )
                return await self._store_response(response, destination)

    async def _store_response(
        self,
        response: httpx.Response,
        destination: Path,
    ) -> DownloadedRemoteCsv:
        media_type = response.headers.get("content-type", "").partition(";")[0].strip().casefold()
        if media_type in _REJECTED_MEDIA_TYPES:
            raise RemoteSourceFailure(
                "remote_source_content_rejected",
                "网页返回的内容不是可用 CSV 文件。",
            )
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                declared_size = int(content_length)
            except ValueError as error:
                raise RemoteSourceFailure(
                    "remote_source_content_rejected",
                    "网页返回的文件长度不合法。",
                ) from error
            if declared_size > self._max_bytes:
                raise RemoteSourceFailure(
                    "remote_source_too_large",
                    "网页 CSV 超过允许的大小。",
                )

        digest = hashlib.sha256()
        size_bytes = 0
        created = False
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as handle:
                created = True
                async for chunk in response.aiter_bytes():
                    size_bytes += len(chunk)
                    if size_bytes > self._max_bytes:
                        raise RemoteSourceFailure(
                            "remote_source_too_large",
                            "网页 CSV 超过允许的大小。",
                        )
                    digest.update(chunk)
                    handle.write(chunk)
            _reject_non_csv_signature(destination)
            try:
                inspection = inspect_csv(destination)
                read_csv_frame(destination, inspection)
            except CsvFormatError as error:
                raise RemoteSourceFailure(
                    "remote_source_csv_invalid",
                    "网页返回的 CSV 结构不合法。",
                ) from error
            return DownloadedRemoteCsv(
                path=destination,
                content_sha256=digest.hexdigest(),
                size_bytes=size_bytes,
                media_type=media_type or "application/octet-stream",
                detected_encoding=inspection.encoding,
            )
        except Exception:
            if created:
                await anyio.Path(destination).unlink(missing_ok=True)
            raise


def _validate_https_url(value: str, *, redirect: bool) -> SplitResult:
    code = "remote_source_redirect_rejected" if redirect else "remote_source_url_rejected"
    message = (
        "第三方数据重定向地址不合法。"
        if redirect
        else "第三方数据地址不符合下载策略。"
    )
    parsed = urlsplit(value)
    if (
        parsed.scheme.casefold() != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise RemoteSourceFailure(code, message)
    try:
        ipaddress.ip_address(parsed.hostname)
    except ValueError:
        pass
    else:
        raise RemoteSourceFailure(code, message)
    return parsed


def _reject_non_csv_signature(path: Path) -> None:
    prefix = path.read_bytes()[:512].lstrip()
    lowered = prefix.lower()
    if (
        not prefix
        or prefix.startswith(b"PK\x03\x04")
        or lowered.startswith((b"<html", b"<!doctype html", b"{", b"["))
    ):
        code = "remote_source_csv_invalid" if not prefix else "remote_source_content_rejected"
        raise RemoteSourceFailure(
            code,
            (
                "网页返回的 CSV 为空。"
                if not prefix
                else "网页返回的内容不是可用 CSV 文件。"
            ),
        )
