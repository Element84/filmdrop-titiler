"""FastAPI application for TiTiler."""

import json
from typing import Annotated, Literal
from logging import config as log_config
from starlette.requests import Request
import rasterio
from starlette.templating import Jinja2Templates
from rio_tiler.io import Reader
import logging
from titiler.core.resources.enums import MediaType

from fastapi import FastAPI, Query
from starlette.middleware.cors import CORSMiddleware
from starlette_cramjam.middleware import CompressionMiddleware
from rio_tiler.io import STACReader
from cogeo_mosaic.backends import MosaicBackend as MosaicJSONBackend
import jinja2
from starlette import status

from titiler.mosaic.errors import MOSAIC_STATUS_CODES
from cogeo_mosaic.errors import MosaicAuthError, MosaicError, MosaicNotFoundError

from filmdrop_titiler.application import __version__ as titiler_version
from filmdrop_titiler.application.settings import ApiSettings
from titiler.core.errors import DEFAULT_STATUS_CODES, add_exception_handlers
from titiler.core.utils import accept_media_type, create_html_response, update_openapi
from titiler.core.factory import (
    MultiBaseTilerFactory,
    TilerFactory,
    TMSFactory,
)
from titiler.mosaic.factory import MosaicTilerFactory
from titiler.core.middleware import (
    CacheControlMiddleware,
    LoggerMiddleware,
    LowerCaseQueryStringMiddleware,
    TotalTimeMiddleware,
)
from titiler.mosaic.extensions.mosaicjson import MosaicJSONExtension
from titiler.mosaic.extensions.wmts import wmtsExtension as mosaic_wmtsExtension
from titiler.extensions import (
    cogValidateExtension,
    cogViewerExtension,
    stacExtension,
    stacRenderExtension,
    stacViewerExtension,
    wmtsExtension,
)
from titiler.core.models.OGC import Conformance, Landing

logging.getLogger("botocore.credentials").disabled = True
logging.getLogger("botocore.utils").disabled = True
logging.getLogger("rasterio.session").setLevel(logging.ERROR)
logging.getLogger("rio-tiler").setLevel(logging.ERROR)

api_settings = ApiSettings()


# custom template directory
templates_location: list[jinja2.BaseLoader] = (
    [jinja2.FileSystemLoader(api_settings.template_directory)]
    if api_settings.template_directory
    else []
)
# default template directory
templates_location.extend(
    [
        jinja2.PackageLoader("filmdrop_titiler.application", "templates"),
        jinja2.PackageLoader("titiler.core", "templates"),
    ]
)

jinja2_env = jinja2.Environment(
    autoescape=jinja2.select_autoescape(["html"]),
    loader=jinja2.ChoiceLoader(templates_location),
)
titiler_templates = Jinja2Templates(env=jinja2_env)


## Create app
app = FastAPI(
    title=api_settings.name,
    openapi_url="/api",
    docs_url="/api.html",
    description=api_settings.description,
    version=titiler_version,
    root_path=api_settings.root_path,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=api_settings.cors_origins,
    allow_credentials=True,
    allow_methods=api_settings.cors_allow_methods,
    allow_headers=["*"],
)

# Fix OpenAPI response header for OGC Common compatibility
update_openapi(app)

TITILER_CONFORMS_TO = {
    "http://www.opengis.net/spec/ogcapi-common-1/1.0/conf/core",
    "http://www.opengis.net/spec/ogcapi-common-1/1.0/conf/landing-page",
    "http://www.opengis.net/spec/ogcapi-common-1/1.0/conf/oas30",
    "http://www.opengis.net/spec/ogcapi-common-1/1.0/conf/html",
    "http://www.opengis.net/spec/ogcapi-common-1/1.0/conf/json",
}

# Simple Dataset endpoints (e.g Cloud Optimized GeoTIFF)
if not api_settings.disable_cog:
    cog = TilerFactory(
        reader=Reader,
        router_prefix="/cog",
        add_ogc_maps=True,
        extensions=[
            cogValidateExtension(),
            cogViewerExtension(),
            stacExtension(),
            wmtsExtension(),
        ],
        enable_telemetry=api_settings.telemetry_enabled,
        templates=titiler_templates,
    )
    cog = TilerFactory(router_prefix="/cog")
    app.include_router(cog.router, prefix="/cog", tags=["Cloud Optimized GeoTIFF"])
    TITILER_CONFORMS_TO.update(cog.conforms_to)

# STAC Endpoints
if not api_settings.disable_stac:
    stac = MultiBaseTilerFactory(
        reader=STACReader,
        router_prefix="/stac",
        add_ogc_maps=True,
        extensions=[stacViewerExtension(), stacRenderExtension(), wmtsExtension()],
        enable_telemetry=api_settings.telemetry_enabled,
        templates=titiler_templates,
    )
    app.include_router(
        stac.router, prefix="/stac", tags=["SpatioTemporal Asset Catalog"]
    )
    TITILER_CONFORMS_TO.update(stac.conforms_to)

# Mosaic endpoints
if not api_settings.disable_mosaic:
    mosaic = MosaicTilerFactory(
        backend=MosaicJSONBackend,  # type: ignore
        router_prefix="/mosaicjson",
        extensions=[
            MosaicJSONExtension(),
            mosaic_wmtsExtension(),
        ],
        enable_telemetry=api_settings.telemetry_enabled,
        templates=titiler_templates,
    )
    app.include_router(mosaic.router, prefix="/mosaicjson", tags=["MosaicJSON"])
    TITILER_CONFORMS_TO.update(mosaic.conforms_to)

# TileMatrixSets endpoints
tms = TMSFactory(templates=titiler_templates)
app.include_router(tms.router, tags=["Tiling Schemes"])
TITILER_CONFORMS_TO.update(tms.conforms_to)

add_exception_handlers(app, DEFAULT_STATUS_CODES)

# Add Mosaic specific error handlers
MOSAIC_STATUS_CODES.update(
    {
        MosaicAuthError: status.HTTP_401_UNAUTHORIZED,
        MosaicError: status.HTTP_424_FAILED_DEPENDENCY,
        MosaicNotFoundError: status.HTTP_404_NOT_FOUND,
    }
)
add_exception_handlers(app, MOSAIC_STATUS_CODES)

# Set all CORS enabled origins
if api_settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=api_settings.cors_origins,
        allow_credentials=True,
        allow_methods=api_settings.cors_allow_methods,
        allow_headers=["*"],
    )

app.add_middleware(
    CompressionMiddleware,
    minimum_size=0,
    exclude_mediatype={
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/jp2",
        "image/webp",
    },
)


app.add_middleware(
    CacheControlMiddleware,
    cachecontrol=api_settings.cachecontrol,
    exclude_path={r"/healthz"},
)

if api_settings.debug:
    app.add_middleware(LoggerMiddleware)
    app.add_middleware(TotalTimeMiddleware)

    log_config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "detailed": {
                    "format": "%(asctime)s - %(levelname)s - %(name)s - %(message)s"
                },
                "request": {
                    "format": (
                        "%(asctime)s - %(levelname)s - %(name)s - %(message)s "
                        + json.dumps(
                            {
                                k: f"%({k})s"
                                for k in [
                                    "http.method",
                                    "http.referer",
                                    "http.request.header.origin",
                                    "http.route",
                                    "http.target",
                                    "http.request.header.content-length",
                                    "http.request.header.accept-encoding",
                                    "http.request.header.origin",
                                    "titiler.path_params",
                                    "titiler.query_params",
                                ]
                            }
                        )
                    ),
                },
            },
            "handlers": {
                "console_detailed": {
                    "class": "logging.StreamHandler",
                    "level": "WARNING",
                    "formatter": "detailed",
                    "stream": "ext://sys.stdout",
                },
                "console_request": {
                    "class": "logging.StreamHandler",
                    "level": "DEBUG",
                    "formatter": "request",
                    "stream": "ext://sys.stdout",
                },
            },
            "loggers": {
                "titiler": {
                    "level": "INFO",
                    "handlers": ["console_detailed"],
                    "propagate": False,
                },
                "titiler.requests": {
                    "level": "INFO",
                    "handlers": ["console_request"],
                    "propagate": False,
                },
            },
        }
    )


if api_settings.lower_case_query_parameters:
    app.add_middleware(LowerCaseQueryStringMiddleware)


@app.get(
    "/healthz",
    description="Health Check.",
    summary="Health Check.",
    operation_id="healthCheck",
    tags=["Health Check"],
)
def application_health_check():
    """Health check."""
    return {
        "versions": {
            "titiler": titiler_version,
            "rasterio": rasterio.__version__,
            "gdal": rasterio.__gdal_version__,
            "proj": rasterio.__proj_version__,
            "geos": rasterio.__geos_version__,
        }
    }


@app.get(
    "/",
    response_model=Landing,
    response_model_exclude_none=True,
    responses={
        200: {
            "content": {
                "text/html": {},
                "application/json": {},
            }
        },
    },
    tags=["OGC Common"],
)
def landing(
    request: Request,
    f: Annotated[
        Literal["html", "json"] | None,
        Query(
            description="Response MediaType. Defaults to endpoint's default or value defined in `accept` header."
        ),
    ] = None,
):
    """TiTiler landing page."""
    data = {
        "title": "TiTiler",
        "description": "A modern dynamic tile server built on top of FastAPI and Rasterio/GDAL.",
        "links": [
            {
                "title": "Landing page",
                "href": str(request.url_for("landing")),
                "type": "text/html",
                "rel": "self",
            },
            {
                "title": "The API definition (JSON)",
                "href": str(request.url_for("openapi")),
                "type": "application/vnd.oai.openapi+json;version=3.0",
                "rel": "service-desc",
            },
            {
                "title": "The API documentation",
                "href": str(request.url_for("swagger_ui_html")),
                "type": "text/html",
                "rel": "service-doc",
            },
            {
                "title": "Conformance Declaration",
                "href": str(request.url_for("conformance")),
                "type": "text/html",
                "rel": "http://www.opengis.net/def/rel/ogc/1.0/conformance",
            },
            {
                "title": "List of Available TileMatrixSets",
                "href": str(request.url_for("tilematrixsets")),
                "type": "application/json",
                "rel": "http://www.opengis.net/def/rel/ogc/1.0/tiling-schemes",
            },
            {
                "title": "List of Available Algorithms",
                "href": str(request.url_for("available_algorithms")),
                "type": "application/json",
                "rel": "data",
            },
            {
                "title": "List of Available ColorMaps",
                "href": str(request.url_for("available_colormaps")),
                "type": "application/json",
                "rel": "data",
            },
            {
                "title": "TiTiler Documentation (external link)",
                "href": "https://developmentseed.org/titiler/",
                "type": "text/html",
                "rel": "doc",
            },
            {
                "title": "TiTiler source code (external link)",
                "href": "https://github.com/developmentseed/titiler",
                "type": "text/html",
                "rel": "doc",
            },
        ],
    }

    if f:
        output_type = MediaType[f]
    else:
        accepted_media = [MediaType.html, MediaType.json]
        output_type = (
            accept_media_type(request.headers.get("accept", ""), accepted_media)
            or MediaType.json
        )

    if output_type == MediaType.html:
        return create_html_response(
            request,
            data,
            title="TiTiler",
            template_name="landing",
            templates=titiler_templates,
        )

    return data


@app.get(
    "/conformance",
    response_model=Conformance,
    response_model_exclude_none=True,
    responses={
        200: {
            "content": {
                "text/html": {},
                "application/json": {},
            }
        },
    },
    tags=["OGC Common"],
)
def conformance(
    request: Request,
    f: Annotated[
        Literal["html", "json"] | None,
        Query(
            description="Response MediaType. Defaults to endpoint's default or value defined in `accept` header."
        ),
    ] = None,
):
    """Conformance classes.

    Called with `GET /conformance`.

    Returns:
        Conformance classes which the server conforms to.

    """
    data = {"conformsTo": sorted(TITILER_CONFORMS_TO)}

    if f:
        output_type = MediaType[f]
    else:
        accepted_media = [MediaType.html, MediaType.json]
        output_type = (
            accept_media_type(request.headers.get("accept", ""), accepted_media)
            or MediaType.json
        )

    if output_type == MediaType.html:
        return create_html_response(
            request,
            data,
            title="Conformance",
            template_name="conformance",
            templates=titiler_templates,
        )

    return data


@app.get("/healthz", description="Health Check", tags=["Health Check"])
def healthz():
    """Health check endpoint."""
    return {"status": "ok"}
