from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field

APP_NAME = "paperdeck"
DEFAULT_BASE_URL = "https://api.openalex.org"


def config_dir() -> Path:
    root = os.environ.get("XDG_CONFIG_HOME")
    base = Path(root) if root else Path.home() / ".config"
    return base / APP_NAME


def config_path() -> Path:
    return config_dir() / "config.toml"


def default_db_path() -> Path:
    root = os.environ.get("XDG_DATA_HOME")
    base = Path(root) if root else Path.home() / ".local" / "share"
    return base / APP_NAME / "paperdeck.db"


def _read_toml(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _read_dotenv(path: Path) -> dict[str, str]:
    try:
        from dotenv import dotenv_values
    except ImportError:
        return {}
    if not path.exists():
        return {}
    return {k: v for k, v in dotenv_values(path).items() if v is not None}


class Settings(BaseModel):
    openalex_api_key: str | None = None
    openalex_base_url: str = DEFAULT_BASE_URL
    db_path: Path = Field(default_factory=default_db_path)
    request_timeout: float = 30.0
    user_agent: str = "paperdeck/0.1 (+https://github.com/)"

    @property
    def has_key(self) -> bool:
        return bool(self.openalex_api_key)


def load_settings(api_key: str | None = None) -> Settings:
    toml_data = _read_toml(config_path()).get("openalex", {})
    dotenv_data = _read_dotenv(Path.cwd() / ".env")

    def pick(name: str, default: str | None = None) -> str | None:
        if name in os.environ and os.environ[name] != "":
            return os.environ[name]
        if name in dotenv_data and dotenv_data[name] != "":
            return dotenv_data[name]
        return default

    resolved_key = (
        api_key
        or pick("OPENALEX_API_KEY")
        or toml_data.get("api_key")
    )
    base_url = pick("OPENALEX_BASE_URL", toml_data.get("base_url", DEFAULT_BASE_URL))
    db_path = pick("PAPERDECK_DB_PATH")

    return Settings(
        openalex_api_key=resolved_key or None,
        openalex_base_url=base_url or DEFAULT_BASE_URL,
        db_path=Path(db_path) if db_path else default_db_path(),
    )


def save_api_key(key: str) -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = (
        "[openalex]\n"
        f'api_key = "{key.strip()}"\n'
    )
    path.write_text(content, encoding="utf-8")
    try:
        path.chmod(0o600)
    except OSError:
        pass
    return path
