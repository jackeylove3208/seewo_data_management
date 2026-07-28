import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit

_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_TRAILING_PUNCTUATION = ".,;:!?)]}，。；：！？）》】"
_REDACTED_FALLBACK = "[远程CSV来源:已隐藏]"
_MAX_URL_LENGTH = 2048


class RemoteSourceRegistrationError(ValueError):
    def __init__(self, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


@dataclass(frozen=True)
class ExtractedConversationLink:
    original_url: str
    display_origin: str
    redacted_message: str


def extract_conversation_link(message: str) -> ExtractedConversationLink | None:
    matches = _candidate_urls(message)
    if not matches:
        return None
    if len(matches) != 1:
        raise RemoteSourceRegistrationError(
            "remote_source_multiple_links",
            "请一次只发送一个第三方 CSV 链接。",
        )
    original_url = matches[0]
    parsed, display_origin = _validate_registration_url(original_url)
    del parsed
    return ExtractedConversationLink(
        original_url=original_url,
        display_origin=display_origin,
        redacted_message=message.replace(
            original_url,
            f"[远程CSV来源:{display_origin}]",
            1,
        ),
    )


def redact_conversation_links(text: str) -> str:
    redacted = text
    for candidate in _candidate_urls(text):
        try:
            _parsed, display_origin = _validate_registration_url(candidate)
            replacement = f"[远程CSV来源:{display_origin}]"
        except RemoteSourceRegistrationError:
            replacement = _REDACTED_FALLBACK
        redacted = redacted.replace(candidate, replacement, 1)
    return redacted


def _candidate_urls(text: str) -> tuple[str, ...]:
    return tuple(
        candidate
        for match in _URL_PATTERN.finditer(text)
        if (candidate := match.group(0).rstrip(_TRAILING_PUNCTUATION))
    )


def _validate_registration_url(value: str) -> tuple[SplitResult, str]:
    if len(value) > _MAX_URL_LENGTH or any(ord(character) < 32 for character in value):
        raise RemoteSourceRegistrationError(
            "remote_source_invalid_url",
            "第三方数据链接格式不正确。",
        )
    parsed = urlsplit(value)
    if parsed.scheme.casefold() != "https":
        raise RemoteSourceRegistrationError(
            "remote_source_https_required",
            "第三方数据链接必须使用 HTTPS。",
        )
    if parsed.username is not None or parsed.password is not None:
        raise RemoteSourceRegistrationError(
            "remote_source_credentials_forbidden",
            "第三方数据链接不能包含账号或密码。",
        )
    if parsed.fragment:
        raise RemoteSourceRegistrationError(
            "remote_source_invalid_url",
            "第三方数据链接不能包含页面片段。",
        )
    hostname = parsed.hostname
    if hostname is None:
        raise RemoteSourceRegistrationError(
            "remote_source_invalid_url",
            "第三方数据链接缺少有效域名。",
        )
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise RemoteSourceRegistrationError(
            "remote_source_ip_literal_forbidden",
            "第三方数据链接必须使用公开域名。",
        )
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii").casefold()
        port = parsed.port
    except (UnicodeError, ValueError) as error:
        raise RemoteSourceRegistrationError(
            "remote_source_invalid_url",
            "第三方数据链接域名或端口不正确。",
        ) from error
    if not ascii_hostname or "." not in ascii_hostname:
        raise RemoteSourceRegistrationError(
            "remote_source_invalid_url",
            "第三方数据链接必须使用完整公开域名。",
        )
    display_origin = (
        ascii_hostname if port in {None, 443} else f"{ascii_hostname}:{port}"
    )
    return parsed, display_origin
