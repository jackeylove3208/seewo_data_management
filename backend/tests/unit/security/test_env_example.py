from pathlib import Path


def test_example_contains_no_usable_api_key() -> None:
    example = (Path(__file__).resolve().parents[3] / ".env.example").read_text()

    assert "replace-with-real-secret" in example
    assert "sk-" not in example
