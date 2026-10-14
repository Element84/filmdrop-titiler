def test_mosaic_tile_routes_use_tiling_size(app):
    paths = app.get("/api").json()["paths"]
    tile_paths = [
        path
        for path in paths
        if path.startswith("/mosaicjson/mosaics/") and "/tiles/" in path
    ]

    assert set(tile_paths) == {
        "/mosaicjson/mosaics/{mosaic_id}/tiles/{z}/{x}/{y}",
        "/mosaicjson/mosaics/{mosaic_id}/tiles/{z}/{x}/{y}.{format}",
    }
    for path in tile_paths:
        parameters = {param["name"] for param in paths[path]["get"]["parameters"]}
        assert "tilesize" in parameters

    tilejson_path = "/mosaicjson/mosaics/{mosaic_id}/tilejson.json"
    tilejson_parameters = {
        param["name"] for param in paths[tilejson_path]["get"]["parameters"]
    }
    assert "tilesize" in tilejson_parameters
    assert "tile_scale" not in tilejson_parameters

    wmts_path = "/mosaicjson/mosaics/{mosaic_id}/WMTSCapabilities.xml"
    wmts_parameters = {param["name"] for param in paths[wmts_path]["get"]["parameters"]}
    assert "tile_scale" not in wmts_parameters
