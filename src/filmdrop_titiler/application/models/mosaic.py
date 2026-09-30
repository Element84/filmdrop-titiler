"""Titiler.mosaic Models."""

import re
from typing import Any

from pydantic import BaseModel, validator
from stac_pydantic.api.search import ExtendedSearch
from stac_pydantic.links import Link


class MosaicEntity(BaseModel):
    """Mosaic Model."""

    id: str
    links: list[Link]


# Regex to validate STAC query datetime strings as valid RFC3339 timestamps
rfc3339_regex_str = (
    r"^(\d\d\d\d)\-(\d\d)\-(\d\d)(T|t)"
    r"(\d\d):(\d\d):(\d\d)(\.\d+)?(Z|([-+])(\d\d):(\d\d))$"
)
rfc3339_regex = re.compile(rfc3339_regex_str)


class StacApiQueryRequestBody(ExtendedSearch):
    """Common request params for MosaicJSON CRUD operations"""

    stac_api_root: str
    asset_name: str | None = None
    name: str | None = None
    description: str | None = None
    attribution: str | None = None
    version: str | None = None

    # overriding limit so we can tell if it's defined or not
    limit: int | None = 10

    max_items: int | None = None

    filter: dict[str, Any] | None = None

    @validator("datetime")
    def validate_datetime(cls, v):
        """
        datetime validation
        overrides default validation due to issue https://github.com/stac-utils/stac-pydantic/issues/78
        """
        if v is None:
            return v
        if "/" in v:
            values = v.split("/")
        else:
            # Single date is interpreted as end date
            values = ["..", v]

        dates = []
        for value in values:
            if value == "..":
                dates.append(value)
                continue
            if not rfc3339_regex.match(value):
                raise ValueError(
                    f"Invalid datetime, must match format ({rfc3339_regex_str})."
                )
            dates.append(value)

        return v


class UrisRequestBody(BaseModel):
    """model for a source body to create a mosaicjson"""

    # option 2 - a list of files and min/max zoom
    urls: list[str]
    minzoom: int | None = None
    maxzoom: int | None = None
    name: str | None = None
    description: str | None = None
    attribution: str | None = None
    version: str | None = None


class TooManyResultsException(Exception):
    """exception when there are too many STAC API results to generate a mosaicjson"""

    def __init__(self, message):
        """init"""
        self.message = message


class StoreException(Exception):
    """exception when there is a problem storing the mosaicjson in the datastore"""

    def __init__(self, message):
        """init"""
        self.message = message


class UnsupportedOperationException(Exception):
    """exception for unsupported operation"""

    def __init__(self, message):
        """init"""
        self.message = message
