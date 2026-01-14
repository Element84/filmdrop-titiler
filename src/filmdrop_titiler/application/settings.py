# """API settings."""

# from pydantic_settings import BaseSettings


# class ApiSettings(BaseSettings):
#     """API settings."""

#     root_path: str = ""
#     cors_origins: str = "*"
#     cors_allow_methods: str = "GET,POST"
#     cachecontrol: str = "public, max-age=3600"
#     debug: bool = False

#     model_config = {
#         "env_prefix": "TITILER_",
#         "env_file": ".env",
#         "extra": "ignore",
#     }

"""Titiler API settings."""

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ApiSettings(BaseSettings):
    """Filmdrop TiTiler application settings."""

    name: str = "Filmdrop TiTiler"
    description: str = """A modern dynamic tile server for the Filmdrop Ecosystem built on top of FastAPI and Rasterio/GDAL.

---

**Documentation**: <a href="https://developmentseed.org/titiler/" target="_blank">https://developmentseed.org/titiler/</a>

**Source Code**: <a href="https://github.com/developmentseed/titiler" target="_blank">https://github.com/developmentseed/titiler</a>

---
    """

    cors_origins: str = "*"
    cors_allow_methods: str = "GET,POST"
    cachecontrol: str = "public, max-age=3600"
    root_path: str = ""
    debug: bool = False

    template_directory: str | None = None

    disable_cog: bool = False
    disable_stac: bool = False
    disable_mosaic: bool = False

    lower_case_query_parameters: bool = False

    telemetry_enabled: bool = False

    # an API key required to access any endpoint, passed via the ?access_token= query parameter
    global_access_token: str | None = None

    model_config = SettingsConfigDict(
        env_prefix="TITILER_API_", env_file=".env", extra="ignore"
    )

    @field_validator("cors_origins")
    def parse_cors_origin(cls, v):
        """Parse CORS origins."""
        return [origin.strip() for origin in v.split(",")]

    @field_validator("cors_allow_methods")
    def parse_cors_allow_methods(cls, v):
        """Parse CORS allowed methods."""
        return [method.strip().upper() for method in v.split(",")]
