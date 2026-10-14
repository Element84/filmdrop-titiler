import gzip
import io
import json
from typing import Any
from urllib.parse import parse_qs, urlparse

import morecantile
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds

from filmdrop_titiler.application import extensions

BOUNDS = (-105.0, 39.0, -104.9, 39.1)
STAC_QUERY_TYPE = "application/vnd.titiler.stac-api-query+json"


def write_cog(path, value: int, count: int = 1) -> None:
    data = np.full((count, 256, 256), value, dtype="uint8")
    with rasterio.open(
        path,
        "w",
        driver="COG",
        width=256,
        height=256,
        count=count,
        dtype="uint8",
        crs="EPSG:4326",
        transform=from_bounds(*BOUNDS, 256, 256),
        nodata=0,
    ) as dst:
        dst.write(data)


@pytest.fixture
def stac_item(tmp_path) -> dict:
    assets: dict[str, dict[str, Any]] = {}
    for name, value, count in (
        ("visual", 200, 3),
        ("red", 10, 1),
        ("green", 20, 1),
        ("blue", 30, 1),
    ):
        href = str(tmp_path / f"{name}.tif")
        write_cog(href, value, count)
        assets[name] = {
            "href": href,
            "type": "image/tiff; application=geotiff; profile=cloud-optimized",
            "title": name,
        }
    assets["red"].update({"raster:scale": 0.0001, "raster:offset": -0.1, "nodata": 0})
    assets["green"]["raster:bands"] = [{"scale": 0.5, "offset": 1.0, "nodata": 0}]
    assets["thumbnail"] = {"href": str(tmp_path / "thumb.jpg"), "type": "image/jpeg"}

    item_path = tmp_path / "item.json"
    minx, miny, maxx, maxy = BOUNDS
    item = {
        "type": "Feature",
        "stac_version": "1.1.0",
        "id": "item",
        "collection": "test",
        "bbox": list(BOUNDS),
        "geometry": {
            "type": "Polygon",
            "coordinates": [
                [[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]]
            ],
        },
        "properties": {"datetime": "2026-01-01T00:00:00Z", "eo:cloud_cover": 5},
        "links": [{"rel": "self", "href": str(item_path)}],
        "assets": assets,
    }
    item_path.write_text(json.dumps(item))
    return item


@pytest.fixture
def stac_search(monkeypatch, tmp_path, stac_item):
    monkeypatch.setenv("TITILER_API_MOSAIC_HOST", str(tmp_path))

    class FakeSearch:
        def items_as_dicts(self):
            return iter([stac_item])

    class FakeClient:
        @staticmethod
        def open(_root):
            return FakeClient()

        def search(self, **_kwargs):
            return FakeSearch()

    monkeypatch.setattr(extensions, "Client", FakeClient)


def create_mosaic(app, multi_asset: bool) -> str:
    response = app.post(
        "/mosaicjson/mosaics",
        headers={"Content-Type": STAC_QUERY_TYPE},
        json={
            "stac_api_root": "https://stac.example.com",
            "collections": ["test"],
            "asset_name": "visual",
            "multi_asset": multi_asset,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def center_tile(app, mosaic_id: str) -> morecantile.Tile:
    mosaicjson = app.get(f"/mosaicjson/mosaics/{mosaic_id}/mosaicjson").json()
    lon, lat, _ = mosaicjson["center"]
    tms = morecantile.tms.get("WebMercatorQuad")
    return tms.tile(lon, lat, mosaicjson["minzoom"])


def tile_url(mosaic_id: str, tile: morecantile.Tile) -> str:
    return f"/mosaicjson/mosaics/{mosaic_id}/tiles/{tile.z}/{tile.x}/{tile.y}.npy"


def read_npy(content: bytes) -> np.ndarray:
    return np.load(io.BytesIO(content))


def test_multi_asset_mosaic_stores_slim_items(app, stac_search, tmp_path):
    mosaic_id = create_mosaic(app, multi_asset=True)

    mosaicjson = app.get(f"/mosaicjson/mosaics/{mosaic_id}/mosaicjson").json()
    assert mosaicjson["asset_type"] == "application/vnd.filmdrop.stac-item-key"
    assert mosaicjson["layers"] == {"default_assets": ["visual"]}
    assert {key for keys in mosaicjson["tiles"].values() for key in keys} == {
        "test/item"
    }

    with gzip.open(tmp_path / f"{mosaic_id}.items.json.gz", "rt") as f:
        items = json.load(f)
    item = items["test/item"]
    assert item["properties"] == {"datetime": "2026-01-01T00:00:00Z"}
    assert item["links"] == []
    assert set(item["assets"]) == {"visual", "red", "green", "blue"}
    assert set(item["assets"]["red"]) == {
        "href",
        "type",
        "raster:scale",
        "raster:offset",
        "nodata",
    }


def test_multi_asset_mosaic_tiles_do_not_fetch_items(app, stac_search, tmp_path):
    mosaic_id = create_mosaic(app, multi_asset=True)
    tile = center_tile(app, mosaic_id)
    (tmp_path / "item.json").unlink()

    response = app.get(tile_url(mosaic_id, tile), params={"assets": "red"})
    assert response.status_code == 200, response.text
    assert read_npy(response.content)[0].max() == 10


def test_unscale_applies_stac_scale_and_offset(app, stac_search):
    mosaic_id = create_mosaic(app, multi_asset=True)
    url = tile_url(mosaic_id, center_tile(app, mosaic_id))

    def first_band(params: dict) -> np.ndarray:
        response = app.get(url, params=params)
        assert response.status_code == 200, response.text
        data = read_npy(response.content)
        # last npy band is the mask; compare valid pixels only
        return data[0][data[-1] > 0]

    # STAC 1.1 asset-level raster:scale/raster:offset: 10 * 0.0001 - 0.1
    assert np.allclose(first_band({"assets": "red", "unscale": True}), -0.099)
    # STAC 1.0 raster:bands scale/offset: 20 * 0.5 + 1
    assert np.allclose(first_band({"assets": "green", "unscale": True}), 11)
    # no STAC scale/offset
    assert np.allclose(first_band({"assets": "blue", "unscale": True}), 30)
    assert first_band({"assets": "red"}).max() == 10
    assert np.allclose(
        first_band({"assets": "red|expression=b1*2", "unscale": True}), -0.198
    )


def test_item_key_mosaicjson_cannot_be_posted(app, stac_search):
    response = app.post(
        "/mosaicjson/mosaics",
        json={
            "mosaicjson": "0.0.3",
            "minzoom": 7,
            "maxzoom": 12,
            "bounds": list(BOUNDS),
            "asset_type": "application/vnd.filmdrop.stac-item-key",
            "tiles": {"0230": ["test/item"]},
        },
    )
    assert response.status_code == 400


def test_item_url_mosaicjson_still_reads_items(app, stac_search, stac_item):
    item_url = stac_item["links"][0]["href"]
    mosaic_id = create_mosaic(app, multi_asset=True)
    mosaicjson = app.get(f"/mosaicjson/mosaics/{mosaic_id}/mosaicjson").json()
    mosaicjson["asset_type"] = "application/geo+json"
    mosaicjson["tiles"] = {qk: [item_url] for qk in mosaicjson["tiles"]}

    response = app.post("/mosaicjson/mosaics", json=mosaicjson)
    assert response.status_code == 201, response.text
    url_mosaic_id = response.json()["id"]

    tile = center_tile(app, url_mosaic_id)
    response = app.get(tile_url(url_mosaic_id, tile), params={"assets": "green"})
    assert response.status_code == 200, response.text
    assert read_npy(response.content)[0].max() == 20


def test_multi_asset_mosaic_tile_selects_assets(app, stac_search):
    mosaic_id = create_mosaic(app, multi_asset=True)
    tile = center_tile(app, mosaic_id)

    default = app.get(tile_url(mosaic_id, tile))
    assert default.status_code == 200, default.text
    # npy output is data bands followed by the mask band
    data = read_npy(default.content)
    assert data.shape[0] == 4
    assert data[0].max() == 200

    rgb = app.get(
        tile_url(mosaic_id, tile),
        params=[("assets", "red"), ("assets", "green"), ("assets", "blue")],
    )
    assert rgb.status_code == 200, rgb.text
    data = read_npy(rgb.content)
    assert data.shape[0] == 4
    assert [data[i].max() for i in range(3)] == [10, 20, 30]

    with_options = app.get(
        tile_url(mosaic_id, tile), params={"assets": "visual|bidx=2"}
    )
    assert with_options.status_code == 200, with_options.text
    assert read_npy(with_options.content).shape[0] == 2


def test_multi_asset_mosaic_rejects_bidx(app, stac_search):
    mosaic_id = create_mosaic(app, multi_asset=True)
    tile = center_tile(app, mosaic_id)

    response = app.get(tile_url(mosaic_id, tile), params={"bidx": 1})
    assert response.status_code == 400
    assert "bidx" in response.json()["detail"]


def test_single_asset_mosaic_rejects_assets(app, stac_search, stac_item):
    mosaic_id = create_mosaic(app, multi_asset=False)
    mosaicjson = app.get(f"/mosaicjson/mosaics/{mosaic_id}/mosaicjson").json()
    assert mosaicjson.get("asset_type") is None
    assert {url for urls in mosaicjson["tiles"].values() for url in urls} == {
        stac_item["assets"]["visual"]["href"]
    }

    tile = center_tile(app, mosaic_id)
    assert app.get(tile_url(mosaic_id, tile)).status_code == 200

    response = app.get(tile_url(mosaic_id, tile), params={"assets": "red"})
    assert response.status_code == 400
    assert "multi-asset" in response.json()["detail"]


def test_tilejson_keeps_repeated_assets(app, stac_search):
    mosaic_id = create_mosaic(app, multi_asset=True)

    response = app.get(
        f"/mosaicjson/mosaics/{mosaic_id}/tilejson.json",
        params=[("assets", "red"), ("assets", "green"), ("assets", "blue")],
    )
    assert response.status_code == 200, response.text
    query = parse_qs(urlparse(response.json()["tiles"][0]).query)
    assert query["assets"] == ["red", "green", "blue"]
    assert query["tilesize"] == ["512"]
