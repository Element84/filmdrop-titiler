from filmdrop_titiler.application import __version__ as titiler_version
import rasterio


def test_health(app):
    response = app.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {
        "versions": {
            "titiler": titiler_version,
            "rasterio": rasterio.__version__,
            "gdal": rasterio.__gdal_version__,
            "proj": rasterio.__proj_version__,
            "geos": rasterio.__geos_version__,
        }
    }
