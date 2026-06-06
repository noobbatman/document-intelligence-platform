from pathlib import Path

from app.core.config import Settings


def test_env_example_bootstrap_and_app_import() -> None:
    project_root = Path(__file__).resolve().parents[1]
    settings = Settings(_env_file=project_root / ".env.example")

    from app.main import app

    openapi_schema = app.openapi()

    assert settings.api_keys == []
    assert app.title
    assert openapi_schema["info"]["title"] == app.title
