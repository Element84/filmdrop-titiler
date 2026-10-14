"""Slimmed STAC Items stored with a mosaic, so tiles never fetch Items from a STAC API."""

import gzip
import json
import re
import time
from threading import Lock
from typing import Any, cast

import attr
import numpy
import pystac
from cachetools import TTLCache
from cogeo_mosaic.backends import DynamoDBBackend
from cogeo_mosaic.backends.file import FileBackend
from cogeo_mosaic.cache import cache_config
from cogeo_mosaic.errors import MosaicError
from rasterio.crs import CRS
from rio_tiler.constants import WGS84_CRS
from rio_tiler.expression import parse_expression
from rio_tiler.io import BaseReader, Reader, STACReader
from rio_tiler.io.stac import DEFAULT_VALID_TYPE
from rio_tiler.models import ImageData, PointData
from rio_tiler.types import AssetInfo, AssetType, BBox, Indexes
from rio_tiler.utils import cast_to_sequence

# MosaicJSON `asset_type` marking tile entries as keys of Items stored with the mosaic
STAC_ITEM_KEY_ASSET_TYPE = "application/vnd.filmdrop.stac-item-key"

# DynamoDB sort key prefix; keeps Item records apart from quadkey records
ITEM_QUADKEY_PREFIX = "item#"

# Asset fields STACReader reads, plus STAC 1.1 scale/offset/nodata it does not apply
ITEM_ASSET_FIELDS = {
    "href",
    "type",
    "roles",
    "bands",
    "raster:bands",
    "eo:bands",
    "alternate",
    "file:header_size",
    "raster:scale",
    "raster:offset",
    "nodata",
}
ITEM_DATETIME_FIELDS = ("datetime", "start_datetime", "end_datetime")

DYNAMODB_BATCH_GET_LIMIT = 100
DYNAMODB_BATCH_GET_ATTEMPTS = 5

_cache: TTLCache = TTLCache(maxsize=max(cache_config.maxsize, 1), ttl=cache_config.ttl)
_cache_lock = Lock()


def item_key(feature: dict) -> str:
    return f"{feature.get('collection', '')}/{feature['id']}"


def slim_item(feature: dict) -> dict:
    """Keep only what STACReader needs to read raster assets."""
    properties = feature.get("properties") or {}
    return {
        "type": "Feature",
        "stac_version": feature.get("stac_version", "1.0.0"),
        "id": feature["id"],
        "collection": feature.get("collection"),
        "bbox": feature.get("bbox"),
        "geometry": feature.get("geometry"),
        "properties": {
            k: properties[k] for k in ITEM_DATETIME_FIELDS if k in properties
        },
        "links": [],
        "assets": {
            name: {k: v for k, v in asset.items() if k in ITEM_ASSET_FIELDS}
            for name, asset in (feature.get("assets") or {}).items()
            if asset.get("type") is None or asset["type"] in DEFAULT_VALID_TYPE
        },
    }


def _file_items_path(backend: FileBackend) -> str:
    return re.sub(r"\.json(\.gz)?$", "", backend.input) + ".items.json.gz"


def write_items(backend: Any, items: dict[str, dict]) -> None:
    if isinstance(backend, DynamoDBBackend):
        with backend.table.batch_writer() as batch:
            for key, item in items.items():
                batch.put_item(
                    {
                        "mosaicId": backend.mosaic_name,
                        "quadkey": ITEM_QUADKEY_PREFIX + key,
                        # a JSON string avoids DynamoDB's float -> Decimal conversion
                        "item": json.dumps(item),
                    }
                )
    elif isinstance(backend, FileBackend):
        with gzip.open(_file_items_path(backend), "wt") as f:
            json.dump(items, f)
    else:
        raise MosaicError(
            f"Storing STAC Items is not supported by the {backend._backend_name} backend"
        )


def _fetch_dynamodb_items(backend: DynamoDBBackend, keys: list[str]) -> dict[str, dict]:
    found: dict[str, dict] = {}
    for start in range(0, len(keys), DYNAMODB_BATCH_GET_LIMIT):
        request: dict = {
            backend.table_name: {
                "Keys": [
                    {
                        "mosaicId": backend.mosaic_name,
                        "quadkey": ITEM_QUADKEY_PREFIX + k,
                    }
                    for k in keys[start : start + DYNAMODB_BATCH_GET_LIMIT]
                ],
                "ProjectionExpression": "quadkey, #item",
                "ExpressionAttributeNames": {"#item": "item"},
            }
        }
        for attempt in range(DYNAMODB_BATCH_GET_ATTEMPTS):
            resp = backend.client.batch_get_item(RequestItems=request)
            for record in resp["Responses"].get(backend.table_name, []):
                key = record["quadkey"].removeprefix(ITEM_QUADKEY_PREFIX)
                found[key] = json.loads(record["item"])
            request = resp.get("UnprocessedKeys") or {}
            if not request:
                break
            time.sleep(0.05 * 2**attempt)
        else:
            raise MosaicError("DynamoDB did not return all stored STAC Items")
    return found


def _fetch_file_items(backend: FileBackend) -> dict[str, dict]:
    try:
        with gzip.open(_file_items_path(backend), "rt") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def read_items(backend: Any, keys: list[str]) -> dict[str, dict]:
    """Return stored Items for `keys`, fetching only those not cached."""
    with _cache_lock:
        items = {
            k: _cache[(backend.input, k)] for k in keys if (backend.input, k) in _cache
        }

    if missing := [k for k in keys if k not in items]:
        if isinstance(backend, DynamoDBBackend):
            fetched = _fetch_dynamodb_items(backend, missing)
        elif isinstance(backend, FileBackend):
            fetched = _fetch_file_items(backend)
        else:
            raise MosaicError(
                f"Reading STAC Items is not supported by the {backend._backend_name} backend"
            )

        if not_found := [k for k in missing if k not in fetched]:
            raise MosaicError(f"Stored STAC Items not found: {not_found}")

        if cache_config.maxsize:
            with _cache_lock:
                for k in missing:
                    _cache[(backend.input, k)] = fetched[k]
        items.update({k: fetched[k] for k in missing})

    return items


@attr.s
class STACScaledReader(Reader):
    """Reader applying STAC raster scale/offset/nodata on `unscale` when the COG has none."""

    # one value per band, or a single value for every band
    stac_scales: list[float] | None = attr.ib(default=None)
    stac_offsets: list[float] | None = attr.ib(default=None)
    stac_nodata: float | None = attr.ib(default=None)

    def _unscale_options(
        self, indexes: Indexes | None, expression: str | None, kwargs: dict
    ) -> dict:
        kwargs = {**self.options, **kwargs}
        if not kwargs.get("unscale") or self.stac_scales is None:
            return kwargs
        dataset = cast(Any, self.dataset)
        # GDAL scale/offset in the COG take precedence over STAC metadata
        if any(s != 1 for s in dataset.scales) or any(o != 0 for o in dataset.offsets):
            return kwargs

        if expression:
            indexes = parse_expression(expression)
        bands = cast_to_sequence(indexes) or range(1, dataset.count + 1)
        scales = numpy.array([_band_value(self.stac_scales, b, 1.0) for b in bands])
        offsets = numpy.array(
            [_band_value(self.stac_offsets or [0.0], b, 0.0) for b in bands]
        )
        shape = (-1, 1, 1)
        previous = kwargs.get("post_process")

        def post_process(data: numpy.ma.MaskedArray) -> numpy.ma.MaskedArray:
            data = cast(numpy.ma.MaskedArray, data.astype("float32", casting="unsafe"))
            numpy.multiply(data, scales.reshape(shape), out=data, casting="unsafe")
            numpy.add(data, offsets.reshape(shape), out=data, casting="unsafe")
            return previous(data) if previous else data

        kwargs["unscale"] = False
        kwargs["post_process"] = post_process
        if kwargs.get("nodata") is None and dataset.nodata is None:
            kwargs["nodata"] = self.stac_nodata
        return kwargs

    def part(
        self,
        bbox: BBox,
        dst_crs: CRS | None = None,
        bounds_crs: CRS = WGS84_CRS,
        indexes: Indexes | None = None,
        expression: str | None = None,
        max_size: int | None = None,
        height: int | None = None,
        width: int | None = None,
        buffer: float | None = None,
        **kwargs: Any,
    ) -> ImageData:
        kwargs = self._unscale_options(indexes, expression, kwargs)
        return super().part(
            bbox,
            dst_crs,
            bounds_crs,
            indexes=indexes,
            expression=expression,
            max_size=max_size,
            height=height,
            width=width,
            buffer=buffer,
            **kwargs,
        )

    def read(
        self,
        indexes: Indexes | None = None,
        expression: str | None = None,
        **kwargs: Any,
    ) -> ImageData:
        kwargs = self._unscale_options(indexes, expression, kwargs)
        return super().read(indexes=indexes, expression=expression, **kwargs)

    def point(
        self,
        lon: float,
        lat: float,
        coord_crs: CRS = WGS84_CRS,
        indexes: Indexes | None = None,
        expression: str | None = None,
        **kwargs: Any,
    ) -> PointData:
        kwargs = self._unscale_options(indexes, expression, kwargs)
        return super().point(
            lon, lat, coord_crs, indexes=indexes, expression=expression, **kwargs
        )


def _band_value(values: list[float], band: int, default: float) -> float:
    if len(values) == 1:
        return values[0]
    return values[band - 1] if band <= len(values) else default


def stac_scale_options(asset: dict) -> dict:
    """STACScaledReader options from STAC 1.1 asset/band or STAC 1.0 raster:bands fields."""
    bands = asset.get("bands") or asset.get("raster:bands") or [{}]
    scale = asset.get("raster:scale", 1.0)
    offset = asset.get("raster:offset", 0.0)
    scales = [float(b.get("raster:scale", b.get("scale", scale))) for b in bands]
    offsets = [float(b.get("raster:offset", b.get("offset", offset))) for b in bands]
    nodata = asset.get("nodata", bands[0].get("nodata"))

    options: dict = {"stac_nodata": nodata}
    if any(s != 1 for s in scales) or any(o != 0 for o in offsets):
        options.update(stac_scales=scales, stac_offsets=offsets)
    return options


@attr.s
class StoredItemSTACReader(STACReader):
    """STACReader taking its Item from `items[input]` instead of fetching `input`."""

    items: dict[str, dict] = attr.ib(factory=dict)
    reader: type[BaseReader] = attr.ib(default=STACScaledReader)

    def __attrs_post_init__(self):
        self.item = pystac.Item.from_dict(self.items[self.input])
        super().__attrs_post_init__()

    def _get_asset_info(self, asset: AssetType) -> AssetInfo:
        info = super()._get_asset_info(asset)
        if issubclass(self.reader, STACScaledReader):
            info["reader_options"].update(stac_scale_options(info["metadata"]))
        return info
