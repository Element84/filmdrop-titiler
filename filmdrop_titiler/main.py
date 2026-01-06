"""FastAPI application for TiTiler."""

import logging

from fastapi import FastAPI
from starlette.middleware.cors import CORSMiddleware
from starlette_cramjam.middleware import CompressionMiddleware

from filmdrop_titiler import __version__
from filmdrop_titiler.settings import ApiSettings
from titiler.core.errors import DEFAULT_STATUS_CODES, add_exception_handlers
from titiler.core.factory import AlgorithmFactory, MultiBaseTilerFactory, TilerFactory, TMSFactory
from titiler.core.middleware import (
    CacheControlMiddleware,
    LoggerMiddleware,
    LowerCaseQueryStringMiddleware,
    TotalTimeMiddleware,
)

logging.getLogger("botocore.credentials").disabled = True
logging.getLogger("botocore.utils").disabled = True
logging.getLogger("rio-tiler").setLevel(logging.ERROR)

api_settings = ApiSettings()

app = FastAPI(
    title="filmdrop-titiler",
    description="A lightweight TiTiler deployment for AWS Lambda",
    version=__version__,
    root_path=api_settings.root_path,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=api_settings.cors_origins.split(","),
    allow_credentials=True,
    allow_methods=api_settings.cors_allow_methods.split(","),
    allow_headers=["*"],
)

# Add compression middleware
app.add_middleware(CompressionMiddleware)

# Add custom middlewares
app.add_middleware(CacheControlMiddleware, cachecontrol=api_settings.cachecontrol)
app.add_middleware(TotalTimeMiddleware)
app.add_middleware(LoggerMiddleware)
app.add_middleware(LowerCaseQueryStringMiddleware)

# Add exception handlers
add_exception_handlers(app, DEFAULT_STATUS_CODES)

# Register TiTiler endpoints
cog = TilerFactory(router_prefix="/cog")
app.include_router(cog.router, prefix="/cog", tags=["Cloud Optimized GeoTIFF"])

stac = MultiBaseTilerFactory(router_prefix="/stac")
app.include_router(stac.router, prefix="/stac", tags=["SpatioTemporal Asset Catalog"])

algorithms = AlgorithmFactory()
app.include_router(algorithms.router, tags=["Algorithms"])

tms = TMSFactory()
app.include_router(tms.router, tags=["Tiling Schemes"])


@app.get("/healthz", description="Health Check", tags=["Health Check"])
def healthz():
    """Health check endpoint."""
    return {"status": "ok"}
