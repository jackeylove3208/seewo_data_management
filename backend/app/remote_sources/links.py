import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import SplitResult, urlsplit, urlunsplit

_URL_PATTERN = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_CSV_SUFFIX_PATTERN = re.compile(r"\.csv", re.IGNORECASE)
_TRAILING_PUNCTUATION = ".,;:!?)]}，。；：！？）》】"
_REDACTED_FALLBACK = "[远程CSV来源:已隐藏]"
_MAX_URL_LENGTH = 2048
_MAX_TRAILING_TEXT_LENGTH = 80


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


@dataclass(frozen=True)
class ConversationLinkCandidate:
    start: int
    end: int
    display_url: str
    trailing_text: str


def conversation_link_candidates(
    message: str,
) -> tuple[ConversationLinkCandidate, ...]:
    matches = _candidate_spans(message)
    if not matches:
        return ()
    if len(matches) != 1:
        raise RemoteSourceRegistrationError(
            "remote_source_multiple_links",
            "请一次只发送一个第三方 CSV 链接。",
        )
    start, broad_end = matches[0]
    broad_url = message[start:broad_end]
    _validate_registration_url(broad_url)
    candidate_ends = {broad_end}
    for suffix_match in _CSV_SUFFIX_PATTERN.finditer(broad_url):
        relative_end = suffix_match.end()
        remainder = broad_url[relative_end:]
        if _looks_like_prose_suffix(remainder):
            candidate_ends.add(start + relative_end)
    return tuple(
        ConversationLinkCandidate(
            start=start,
            end=end,
            display_url=_model_safe_url(message[start:end]),
            trailing_text=redact_conversation_links(
                message[end : end + _MAX_TRAILING_TEXT_LENGTH]
            ),
        )
        for end in sorted(candidate_ends)
    )


def extract_conversation_link(
    message: str,
    *,
    start: int | None = None,
    end: int | None = None,
) -> ExtractedConversationLink | None:
    candidates = conversation_link_candidates(message)
    if not candidates:
        return None
    if (start is None) != (end is None):
        raise RemoteSourceRegistrationError(
            "remote_source_invalid_boundary",
            "第三方数据链接边界不完整，请重新发送链接。",
        )
    if start is None or end is None:
        if len(candidates) != 1:
            raise RemoteSourceRegistrationError(
                "remote_source_boundary_required",
                "第三方数据链接与后续文字相连，请重新确认链接范围。",
            )
        selected = candidates[0]
    else:
        selected_candidate = next(
            (
                candidate
                for candidate in candidates
                if candidate.start == start and candidate.end == end
            ),
            None,
        )
        if selected_candidate is None:
            raise RemoteSourceRegistrationError(
                "remote_source_invalid_boundary",
                "第三方数据链接边界与当前消息不一致，请重新发送链接。",
            )
        selected = selected_candidate
    original_url = message[selected.start : selected.end]
    parsed, display_origin = _validate_registration_url(original_url)
    del parsed
    return ExtractedConversationLink(
        original_url=original_url,
        display_origin=display_origin,
        redacted_message=(
            message[: selected.start]
            + f"[远程CSV来源:{display_origin}]"
            + message[selected.end :]
        ),
    )


def redact_conversation_links(text: str) -> str:
    redacted = text
    for start, end in reversed(_candidate_spans(text)):
        candidate = text[start:end]
        try:
            _parsed, display_origin = _validate_registration_url(candidate)
            replacement = f"[远程CSV来源:{display_origin}]"
        except RemoteSourceRegistrationError:
            replacement = _REDACTED_FALLBACK
        redacted = redacted[:start] + replacement + redacted[end:]
    return redacted


def _candidate_spans(text: str) -> tuple[tuple[int, int], ...]:
    return tuple(
        (match.start(), match.end() - (len(match.group(0)) - len(candidate)))
        for match in _URL_PATTERN.finditer(text)
        if (candidate := match.group(0).rstrip(_TRAILING_PUNCTUATION))
    )


def _looks_like_prose_suffix(value: str) -> bool:
    if not value:
        return False
    suffix = value[1:] if value.startswith("/") else value
    return bool(suffix and _is_cjk(suffix[0]))


def _is_cjk(value: str) -> bool:
    codepoint = ord(value)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
    )


def _model_safe_url(value: str) -> str:
    parsed = urlsplit(value)
    safe_query = "<redacted>" if parsed.query else ""
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, safe_query, parsed.fragment)
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
