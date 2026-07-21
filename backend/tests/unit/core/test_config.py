from pathlib import Path

from app.core.config import DEFAULT_ENV_FILE, Settings


def test_default_env_file_is_backend_absolute_path() -> None:
    assert DEFAULT_ENV_FILE == Path(__file__).resolve().parents[3] / ".env"
    assert DEFAULT_ENV_FILE.is_absolute()
    assert Settings.model_config["env_file"] == DEFAULT_ENV_FILE
