import pytest

from app.remote_sources.links import (
    RemoteSourceRegistrationError,
    conversation_link_candidates,
    extract_conversation_link,
    redact_conversation_links,
)


def test_extracts_one_https_link_and_redacts_query() -> None:
    extracted = extract_conversation_link(
        "请同步 https://data.example.test/roster.csv?secret=value 的学生"
    )

    assert extracted is not None
    assert extracted.original_url.endswith("?secret=value")
    assert extracted.display_origin == "data.example.test"
    assert extracted.redacted_message == "请同步 [远程CSV来源:data.example.test] 的学生"


def test_message_without_link_is_unchanged() -> None:
    assert extract_conversation_link("同步七年级学生") is None
    assert redact_conversation_links("同步七年级学生") == "同步七年级学生"


@pytest.mark.parametrize(
    ("message", "code"),
    [
        ("http://data.example.test/a.csv", "remote_source_https_required"),
        (
            "https://user:pass@data.example.test/a.csv",
            "remote_source_credentials_forbidden",
        ),
        (
            "https://a.example/a.csv https://b.example/b.csv",
            "remote_source_multiple_links",
        ),
        ("https://127.0.0.1/a.csv", "remote_source_ip_literal_forbidden"),
    ],
)
def test_rejects_unsafe_or_multiple_links(message: str, code: str) -> None:
    with pytest.raises(RemoteSourceRegistrationError) as raised:
        extract_conversation_link(message)

    assert raised.value.code == code


def test_redacts_links_in_legacy_history_without_registering_them() -> None:
    assert redact_conversation_links(
        "旧消息 https://data.example.test/a.csv?secret=value"
    ) == "旧消息 [远程CSV来源:data.example.test]"


@pytest.mark.parametrize("suffix", ["的数据", "/的数据"])
def test_model_can_select_csv_boundary_before_chinese_prose(suffix: str) -> None:
    message = f"请同步 https://data.example.test/roster.csv{suffix}"

    candidates = conversation_link_candidates(message)
    csv_candidate = next(
        candidate
        for candidate in candidates
        if message[candidate.start : candidate.end].endswith(".csv")
    )
    extracted = extract_conversation_link(
        message,
        start=csv_candidate.start,
        end=csv_candidate.end,
    )

    assert extracted is not None
    assert extracted.original_url == "https://data.example.test/roster.csv"
    assert extracted.redacted_message == (
        f"请同步 [远程CSV来源:data.example.test]{suffix}"
    )


def test_model_candidates_do_not_expose_signed_query_values() -> None:
    message = (
        "请同步 https://data.example.test/roster.csv?"
        "X-Amz-Credential=secret-value&X-Amz-Signature=private-signature"
    )

    candidates = conversation_link_candidates(message)

    assert len(candidates) == 1
    assert "secret-value" not in candidates[0].display_url
    assert "private-signature" not in candidates[0].display_url
    assert "<redacted>" in candidates[0].display_url
