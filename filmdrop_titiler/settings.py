"""API settings."""

from typing import Optional

from pydantic_settings import BaseSettings


class ApiSettings(BaseSettings):
    """API settings."""

    root_path: str = ""
    cors_origins: str = "*"
    cors_allow_methods: str = "GET,POST"
    cachecontrol: str = "public, max-age=3600"
    debug: bool = False

    model_config = {
        "env_prefix": "TITILER_",
        "env_file": ".env",
        "extra": "ignore",
    }
