import asyncio
import logging
import os
import uuid
from dataclasses import dataclass
from functools import partial
from typing import Annotated, Any
from urllib.parse import urlencode

import morecantile
import rasterio
from cogeo_mosaic.backends import DynamoDBBackend
from cogeo_mosaic.errors import MosaicError
from cogeo_mosaic.mosaic import MosaicJSON
from fastapi import Depends, Header, HTTPException, Path, Query
from pydantic import conint
from pystac_client import Client
from rio_tiler.constants import MAX_THREADS
from rio_tiler.io import Reader
from rio_tiler.utils import Timer
from starlette.requests import Request
from starlette.responses import Response
from starlette.status import (
    HTTP_200_OK,
    HTTP_201_CREATED,
    HTTP_400_BAD_REQUEST,
    HTTP_404_NOT_FOUND,
    HTTP_409_CONFLICT,
    HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    HTTP_500_INTERNAL_SERVER_ERROR,
)
from titiler.core.factory import FactoryExtension, img_endpoint_params
from titiler.core.models.mapbox import TileJSON
from titiler.core.resources.enums import ImageType, MediaType, OptionalHeader
from titiler.core.resources.responses import (
    XMLResponse,
)
from titiler.mosaic.factory import MosaicTilerFactory

from filmdrop_titiler.application.models.mosaic import (
    Link,
    MosaicEntity,
    StacApiQueryRequestBody,
    StoreException,
    TooManyResultsException,
    UrisRequestBody,
)

from .settings import ApiSettings


@dataclass
class mosaicExtension(FactoryExtension):
    default_max_items = 1000

    def register(self, factory: MosaicTilerFactory):
        async def retrieve(
            mosaic_id: str, reader_params, include_tiles: bool = False
        ) -> MosaicJSON | None:
            mosaic_uri = mk_src_path(mosaic_id)

            try:
                return await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(
                        None,  # executor
                        read_mosaicjson_sync,  # func
                        mosaic_uri,
                        reader_params,
                        include_tiles,
                    ),
                    20,
                )
            except TimeoutError as e:
                raise HTTPException(
                    HTTP_500_INTERNAL_SERVER_ERROR,
                    "Error: timeout retrieving mosaic from datastore.",
                ) from e
            except MosaicError:
                return None

        async def store(
            mosaic_id: str, mosaicjson: MosaicJSON, env, overwrite: bool
        ) -> None:
            try:
                existing = await retrieve(mosaic_id, env)
            except Exception:
                existing = False

            if not overwrite and existing:
                raise StoreException("Attempting to create already existing mosaic")
            if overwrite and not existing:
                raise StoreException("Attempting to update non-existant mosaic")

            mosaic_uri = mk_src_path(mosaic_id)

            try:
                await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(
                        None,  # executor
                        mosaic_write,  # func
                        mosaic_uri,
                        mosaicjson,
                        overwrite,
                    ),
                    20,
                )
            except TimeoutError as e:
                raise HTTPException(
                    HTTP_500_INTERNAL_SERVER_ERROR,
                    "Error: timeout storing mosaic in datastore",
                ) from e

        # todo: make this safer in case visual doesn't exist
        # how to handle others?
        # support for selection by role?
        def asset_href(feature: dict, asset_name: str) -> str:
            if href := feature.get("assets", {}).get(asset_name, {}).get("href"):
                return href
            else:
                raise Exception(f"Asset with name '{asset_name}' could not be found.")

        def extract_mosaicjson_from_features(
            features: list[dict], asset_name: str
        ) -> MosaicJSON | None:
            if features:
                try:
                    with Reader(asset_href(features[0], asset_name)) as cog:
                        minzoom = cog.minzoom
                        maxzoom = cog.maxzoom
                    return MosaicJSON.from_features(
                        features,
                        minzoom=minzoom,
                        maxzoom=maxzoom,
                        accessor=partial(asset_href, asset_name=asset_name),
                    )

                # when Item geometry is a MultiPolygon (instead of a Polygon), supermercado raises
                # handle error "local variable 'x' referenced before assignment"
                # supermercado/burntiles.py ", line 38, in _feature_extrema
                # as this method only handles Polygon, LineString, and Point :grimace:
                # https://github.com/mapbox/supermercado/issues/47
                except UnboundLocalError as e:
                    raise Exception(
                        "STAC Items likely have MultiPolygon geometry, and only Polygon is supported."
                    ) from e
                except Exception as e:
                    raise Exception(
                        f"Error extracting mosaic data from results: {e}"
                    ) from e
            else:
                return None

        async def mosaicjson_from_stac_api_query(
            req: StacApiQueryRequestBody,
        ) -> MosaicJSON:
            """Create a mosaic for the given parameters"""

            if not req.stac_api_root:
                raise HTTPException(
                    HTTP_400_BAD_REQUEST,
                    "Error: stac_api_root field must be non-empty.",
                )

            try:
                try:
                    features = await asyncio.wait_for(
                        asyncio.get_running_loop().run_in_executor(
                            None,
                            execute_stac_search,
                            req,  # executor  # func
                        ),
                        30,
                    )
                except TimeoutError as e:
                    raise HTTPException(
                        HTTP_500_INTERNAL_SERVER_ERROR,
                        "Error: timeout executing STAC API search.",
                    ) from e
                except TooManyResultsException as e:
                    raise HTTPException(
                        HTTP_400_BAD_REQUEST,
                        f"Error: too many results from STAC API Search: {e}",
                    ) from e

                if not features:
                    raise HTTPException(
                        HTTP_500_INTERNAL_SERVER_ERROR,
                        "Error: STAC API Search returned no results.",
                    )

                try:
                    mosaicjson = await asyncio.wait_for(
                        asyncio.get_running_loop().run_in_executor(
                            None,
                            extract_mosaicjson_from_features,
                            features,
                            req.asset_name if req.asset_name else "visual",
                        ),
                        60,  # todo: how much time should/can it take?
                    )
                except TimeoutError as e:
                    raise HTTPException(
                        HTTP_500_INTERNAL_SERVER_ERROR,
                        "Error: timeout reading a COG asset and generating MosaicJSON definition",
                    ) from e

                if mosaicjson is None:
                    raise HTTPException(
                        HTTP_500_INTERNAL_SERVER_ERROR,
                        "Error: could not extract mosaic data",
                    )

                mosaicjson.name = req.name
                mosaicjson.description = req.description
                mosaicjson.attribution = req.attribution
                mosaicjson.version = req.version if req.version else "0.0.1"

                return mosaicjson

            except HTTPException as e:
                raise e
            except Exception as e:
                raise HTTPException(
                    HTTP_500_INTERNAL_SERVER_ERROR, f"Error: {e}"
                ) from e

        def mk_src_path(mosaic_id: str) -> str:
            settings = ApiSettings()
            if settings.mosaic_backend == "dynamodb://":
                return f"{settings.mosaic_backend}{settings.mosaic_host}:{mosaic_id}"
            else:
                return f"{settings.mosaic_backend}{settings.mosaic_host}/{mosaic_id}{settings.mosaic_format}"

        def mk_mosaic_entity(mosaic_id, self_uri) -> MosaicEntity:
            return MosaicEntity(
                id=mosaic_id,
                links=[
                    Link(
                        rel="self", href=self_uri, type="application/json", title="Self"
                    ),
                    Link(
                        rel="mosaicjson",
                        href=f"{self_uri}/mosaicjson",
                        type="application/json",
                        title="MosiacJSON",
                    ),
                    Link(
                        rel="tilejson",
                        href=f"{self_uri}/tilejson.json",
                        type="application/json",
                        title="TileJSON",
                    ),
                    Link(
                        rel="tiles",
                        href=f"{self_uri}/tiles/{{z}}/{{x}}/{{y}}",
                        type="application/json",
                        title="Tiles",
                    ),
                    Link(
                        rel="wmts",
                        href=f"{self_uri}/WMTSCapabilities.xml",
                        type="application/json",
                        title="WMTS",
                    ),
                ],
            )

        async def populate_mosaicjson(request, content_type):
            body_json = await request.json()
            content_type = (content_type or "").split(";", 1)[0].strip().lower()
            if content_type in (
                "",
                "application/json",
                "application/vnd.titiler.mosaicjson+json",
            ):
                mosaicjson = MosaicJSON(**body_json)
            elif content_type == "application/vnd.titiler.urls+json":
                mosaicjson = await mosaicjson_from_urls(UrisRequestBody(**body_json))
            elif content_type == "application/vnd.titiler.stac-api-query+json":
                mosaicjson = await mosaicjson_from_stac_api_query(
                    StacApiQueryRequestBody(**body_json)
                )
            else:
                raise HTTPException(
                    HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                    "Error: media in Content-Type header is not supported.",
                )
            return mosaicjson

        def mosaic_write(
            mosaic_uri: str, mosaicjson: MosaicJSON, overwrite: bool
        ) -> None:
            with factory.backend(mosaic_uri, mosaic_def=mosaicjson) as mosaic:
                mosaic.write(overwrite=overwrite)

        def execute_stac_search(mosaic_request: StacApiQueryRequestBody) -> list[dict]:
            try:
                search_result = Client.open(mosaic_request.stac_api_root).search(
                    ids=mosaic_request.ids,
                    collections=mosaic_request.collections,
                    datetime=mosaic_request.datetime,
                    bbox=mosaic_request.bbox,
                    intersects=mosaic_request.intersects,
                    query=mosaic_request.query,
                    filter=mosaic_request.filter,
                    max_items=mosaic_request.max_items
                    if mosaic_request.max_items
                    and mosaic_request.max_items < self.default_max_items
                    else self.default_max_items,
                    limit=mosaic_request.limit if mosaic_request.limit else 100,
                )

                return list(search_result.items_as_dicts())
            except TooManyResultsException as e:
                raise e
            except Exception as e:
                raise Exception(f"STAC Search error: {e}") from e

        def read_mosaicjson_sync(
            mosaic_uri: str, reader_params, include_tiles: bool
        ) -> MosaicJSON:
            with factory.backend(
                mosaic_uri,
                reader=factory.dataset_reader,
                reader_options={**reader_params},
            ) as mosaic:
                mosaicjson = mosaic.mosaic_def
                if include_tiles and isinstance(mosaic, DynamoDBBackend):
                    keys = (mosaic._fetch_dynamodb(qk) for qk in mosaic._quadkeys)
                    mosaicjson.tiles = {x["quadkey"]: x["assets"] for x in keys}
                return mosaicjson

        def render_tile(
            mosaic_uri: str,
            z: int,
            x: int,
            y: int,
            scale: int,
            format: ImageType,
            layer_params,
            dataset_params,
            render_params,
            colormap,
            reader_params,
        ) -> tuple[bytes, Any, ImageType, list[tuple[str, float]]]:
            """Create map tile from a COG."""
            timings = []

            tilesize = scale * 256

            threads = int(os.getenv("MOSAIC_CONCURRENCY", MAX_THREADS))
            with (
                Timer() as t,
                factory.backend(
                    mosaic_uri,
                    reader=factory.dataset_reader,
                    reader_options={**reader_params},
                ) as src_dst,
            ):
                mosaic_read = t.from_start
                timings.append(("mosaicread", round(mosaic_read * 1000, 2)))

                data, _ = src_dst.tile(
                    x,
                    y,
                    z,
                    threads=threads,
                    tilesize=tilesize,
                    **layer_params,
                    **dataset_params,
                )
            timings.append(("dataread", round((t.elapsed - mosaic_read) * 1000, 2)))

            if not format:
                format = ImageType.jpeg if data.mask.all() else ImageType.png

            with Timer() as t:
                image = data.post_process()
            timings.append(("postprocess", round(t.elapsed * 1000, 2)))

            if "rescale" in render_params:
                image.rescale(render_params["rescale"])

            with Timer() as t:
                content = image.render(
                    img_format=format.driver,
                    colormap=colormap,
                    **format.profile,
                    **render_params,
                )
            timings.append(("format", round(t.elapsed * 1000, 2)))

            return content, data.assets, format, timings

        # with dynamodb backend, the tiles field for this is always empty
        # https://github.com/developmentseed/cogeo-mosaic/issues/175
        @factory.router.get(
            "/mosaics/{mosaic_id}",
            response_model=MosaicEntity,
            responses={
                HTTP_200_OK: {
                    "description": "Return a Mosaic resource for the given ID."
                },
                HTTP_404_NOT_FOUND: {
                    "description": "Mosaic resource for the given ID does not exist."
                },
            },
        )
        async def get_mosaic(
            request: Request,
            mosaic_id: str,
            env=Depends(factory.environment_dependency),
            reader_params=Depends(factory.reader_dependency),
        ) -> MosaicEntity:
            self_uri = factory.url_for(request, "get_mosaic", mosaic_id=mosaic_id)
            with rasterio.Env(**env):
                if await retrieve(mosaic_id, reader_params.as_dict()):
                    return mk_mosaic_entity(mosaic_id=mosaic_id, self_uri=self_uri)
                else:
                    raise HTTPException(
                        HTTP_404_NOT_FOUND,
                        "Error: mosaic with given ID does not exist.",
                    )

        @factory.router.get(
            "/mosaics/{mosaic_id}/mosaicjson",
            response_model=MosaicJSON,
            responses={
                200: {
                    "description": "Return a MosaicJSON definition for the given ID."
                },
                404: {
                    "description": "Mosaic resource for the given ID does not exist."
                },
            },
        )
        async def get_mosaic_mosaicjson(
            mosaic_id: str,
            env=Depends(factory.environment_dependency),
            reader_params=Depends(factory.reader_dependency),
        ) -> MosaicJSON:
            with rasterio.Env(**env):
                if m := await retrieve(
                    mosaic_id, reader_params.as_dict(), include_tiles=True
                ):
                    return m
                else:
                    raise HTTPException(
                        HTTP_404_NOT_FOUND,
                        "Error: mosaic with given ID does not exist.",
                    )

        # derived from cogeo.xyz
        @factory.router.get(
            r"/mosaics/{mosaic_id}/tilejson.json",
            response_model=TileJSON,
            responses={
                200: {"description": "Return a tilejson for the given ID."},
                404: {
                    "description": "Mosaic resource for the given ID does not exist."
                },
            },
            response_model_exclude_none=True,
        )
        async def get_mosaic_tilejson(
            mosaic_id: str,
            request: Request,
            tile_format: ImageType | None = Query(
                None, description="Output image type. Default is auto."
            ),
            tile_scale: int = Query(
                1, gt=0, lt=4, description="Tile size scale. 1=256x256, 2=512x512..."
            ),
            minzoom: int | None = Query(None, description="Overwrite default minzoom."),
            maxzoom: int | None = Query(None, description="Overwrite default maxzoom."),
            layer_params=Depends(factory.layer_dependency),  # noqa
            dataset_params=Depends(factory.dataset_dependency),  # noqa
            render_params=Depends(factory.render_dependency),  # noqa
            colormap=Depends(factory.colormap_dependency),  # noqa,
            env=Depends(factory.environment_dependency),
            reader_params=Depends(factory.reader_dependency),
        ) -> TileJSON:
            """Return TileJSON document for a MosaicJSON."""

            kwargs = {
                "mosaic_id": mosaic_id,
                "z": "{z}",
                "x": "{x}",
                "y": "{y}",
                "scale": tile_scale,
            }
            if tile_format:
                kwargs["format"] = tile_format.value
            tiles_url = factory.url_for(request, "tile", **kwargs)

            q = dict(request.query_params)
            q.pop("TileMatrixSetId", None)
            q.pop("tile_format", None)
            q.pop("tile_scale", None)
            qs = urlencode(list(q.items()))
            tiles_url += f"?{qs}"

            with rasterio.Env(**env):
                if mosaicjson := await retrieve(mosaic_id, reader_params.as_dict()):
                    center = list(mosaicjson.center)
                    if minzoom is not None:
                        center[-1] = minzoom
                    return TileJSON(
                        bounds=mosaicjson.bounds,
                        center=tuple(center),
                        minzoom=minzoom if minzoom is not None else mosaicjson.minzoom,
                        maxzoom=maxzoom if maxzoom is not None else mosaicjson.maxzoom,
                        name=mosaic_id,
                        tiles=[tiles_url],
                    )
                else:
                    raise HTTPException(
                        HTTP_404_NOT_FOUND,
                        "Error: mosaic with given ID does not exist.",
                    )

        @factory.router.post(
            "/mosaics",
            status_code=HTTP_201_CREATED,
            responses={
                HTTP_201_CREATED: {"description": "Created a new mosaic"},
                HTTP_409_CONFLICT: {
                    "description": "Conflict while trying to create mosaic"
                },
                HTTP_500_INTERNAL_SERVER_ERROR: {
                    "description": "Mosaic could not be created"
                },
            },
            response_model=MosaicEntity,
        )
        async def post_mosaics(
            mosaic_json: MosaicJSON | UrisRequestBody | StacApiQueryRequestBody,
            request: Request,
            response: Response,
            content_type: str | None = Header(None),
            env=Depends(factory.environment_dependency),
        ) -> MosaicEntity:
            """Create a MosaicJSON"""

            mosaicjson = await populate_mosaicjson(request, content_type)
            mosaic_id = str(uuid.uuid4())

            # duplicate IDs are unlikely to exist, but handle it just to be safe
            try:
                with rasterio.Env(**env):
                    await store(mosaic_id, mosaicjson, env, overwrite=False)
            except StoreException as e:
                raise HTTPException(
                    HTTP_409_CONFLICT, "Error: mosaic with given ID already exists"
                ) from e
            except Exception as e:
                logging.error(f"could not save mosaic: {e}")
                raise HTTPException(
                    HTTP_500_INTERNAL_SERVER_ERROR, "Error: could not save mosaic"
                ) from e

            self_uri = factory.url_for(request, "get_mosaic", mosaic_id=mosaic_id)

            response.headers["Location"] = self_uri

            return mk_mosaic_entity(mosaic_id, self_uri)

        # derived from cogeo-xyz
        @factory.router.get(
            r"/mosaics/{mosaic_id}/tiles/{z}/{x}/{y}", **img_endpoint_params
        )
        @factory.router.get(
            r"/mosaics/{mosaic_id}/tiles/{z}/{x}/{y}.{format}", **img_endpoint_params
        )
        @factory.router.get(
            r"/mosaics/{mosaic_id}/tiles/{z}/{x}/{y}@{scale}x", **img_endpoint_params
        )
        @factory.router.get(
            r"/mosaics/{mosaic_id}/tiles/{z}/{x}/{y}@{scale}x.{format}",
            **img_endpoint_params,
        )
        async def tile(
            mosaic_id: str,
            z: int = Path(..., ge=0, le=30, description="Mercator tiles's zoom level"),
            x: int = Path(..., description="Mercator tiles's column"),
            y: int = Path(..., description="Mercator tiles's row"),
            scale: Annotated[
                conint(gt=0, lt=4), "Tile size scale. 1=256x256, 2=512x512..."
            ] = 1,
            format: Annotated[
                ImageType,
                "Output image type. Default is auto.",
            ] = None,
            layer_params=Depends(factory.layer_dependency),
            dataset_params=Depends(factory.dataset_dependency),
            render_params=Depends(factory.render_dependency),
            colormap=Depends(factory.colormap_dependency),
            env=Depends(factory.environment_dependency),
            reader_params=Depends(factory.reader_dependency),
        ):
            """Create map tile from a mosaic."""

            try:
                with rasterio.Env(**env):
                    (
                        content,
                        data_assets,
                        img_format,
                        timings,
                    ) = await asyncio.wait_for(
                        asyncio.get_running_loop().run_in_executor(
                            None,  # executor
                            render_tile,  # func
                            mk_src_path(mosaic_id),
                            z,
                            x,
                            y,
                            scale,
                            format,
                            layer_params.as_dict(),
                            dataset_params.as_dict(),
                            render_params.as_dict(),
                            colormap,
                            reader_params.as_dict(),
                        ),
                        int(os.getenv("MOSAIC_TILE_TIMEOUT", 30)),
                    )
            except TimeoutError as e:
                raise HTTPException(
                    HTTP_500_INTERNAL_SERVER_ERROR,
                    "Error: timeout executing rendering tile.",
                ) from e

            headers: dict[str, str] = {}

            if OptionalHeader.server_timing in factory.optional_headers:
                headers["Server-Timing"] = ", ".join(
                    [f"{name};dur={time}" for (name, time) in timings]
                )

            if OptionalHeader.x_assets in factory.optional_headers:
                headers["X-Assets"] = ",".join(data_assets)

            return Response(content, media_type=img_format.mediatype, headers=headers)

        @factory.router.get(
            "/mosaics/{mosaic_id}/WMTSCapabilities.xml", response_class=XMLResponse
        )
        def wmts(
            request: Request,
            mosaic_id: str,
            tile_format: ImageType = Query(
                ImageType.png, description="Output image type. Default is png."
            ),
            tile_scale: int = Query(
                1, gt=0, lt=4, description="Tile size scale. 1=256x256, 2=512x512..."
            ),
            minzoom: int | None = Query(None, description="Overwrite default minzoom."),
            maxzoom: int | None = Query(None, description="Overwrite default maxzoom."),
            layer_params=Depends(factory.layer_dependency),  # noqa
            dataset_params=Depends(factory.dataset_dependency),  # noqa
            render_params=Depends(factory.render_dependency),  # noqa
            colormap=Depends(factory.colormap_dependency),  # noqa
        ):
            """OGC WMTS endpoint."""
            if minzoom and maxzoom:
                if minzoom > maxzoom:
                    raise HTTPException(
                        status_code=422,
                        detail="minzoom parameter must be less than or equal to maxzoom",
                    )

            tiles_url = factory.url_for(
                request,
                "tile",
                mosaic_id=mosaic_id,
                z="{TileMatrix}",
                x="{TileCol}",
                y="{TileRow}",
                scale=tile_scale,
                format=tile_format.value,
            )

            q = dict(request.query_params)
            q.pop("tile_format", None)
            q.pop("tile_scale", None)
            q.pop("minzoom", None)
            q.pop("maxzoom", None)
            q.pop("SERVICE", None)
            q.pop("REQUEST", None)
            qs = urlencode(list(q.items()))
            tiles_url += f"?{qs}"

            mosaic_uri = mk_src_path(mosaic_id)

            with factory.backend(mosaic_uri) as src_dst:
                bounds = src_dst.bounds
                minzoom = minzoom if minzoom is not None else src_dst.minzoom
                maxzoom = maxzoom if maxzoom is not None else src_dst.maxzoom

            tms = morecantile.tms.get("WebMercatorQuad")

            tileMatrix = []
            for zoom in range(minzoom, maxzoom + 1):
                matrix = tms.matrix(zoom)
                tm = f"""
                        <TileMatrix>
                            <ows:Identifier>{matrix.id}</ows:Identifier>
                            <ScaleDenominator>{matrix.scaleDenominator / tile_scale}</ScaleDenominator>
                            <TopLeftCorner>{matrix.pointOfOrigin[0]} {matrix.pointOfOrigin[1]}</TopLeftCorner>
                            <TileWidth>{matrix.tileWidth * tile_scale}</TileWidth>
                            <TileHeight>{matrix.tileHeight * tile_scale}</TileHeight>
                            <MatrixWidth>{matrix.matrixWidth}</MatrixWidth>
                            <MatrixHeight>{matrix.matrixHeight}</MatrixHeight>
                        </TileMatrix>"""
                tileMatrix.append(tm)

            return factory.templates.TemplateResponse(
                name="wmts.xml",
                request=request,
                context={
                    "tiles_endpoint": tiles_url,
                    "bounds": bounds,
                    "tileMatrix": tileMatrix,
                    "tms": tms,
                    "title": "Cloud Optimized GeoTIFF",
                    "layer_name": "cogeo",
                    "media_type": tile_format.mediatype,
                },
                media_type=MediaType.xml.value,
            )

        async def mosaicjson_from_urls(urisrb: UrisRequestBody) -> MosaicJSON:
            if len(urisrb.urls) > self.default_max_items:
                raise HTTPException(
                    HTTP_400_BAD_REQUEST,
                    f"Error: a maximum of {self.default_max_items} URLs can be mosaiced.",
                )

            try:
                mosaicjson = await asyncio.wait_for(
                    asyncio.get_running_loop().run_in_executor(
                        None,  # executor
                        lambda: MosaicJSON.from_urls(
                            urls=urisrb.urls,
                            minzoom=urisrb.minzoom,
                            maxzoom=urisrb.maxzoom,
                            max_threads=int(
                                os.getenv("MOSAIC_CONCURRENCY", MAX_THREADS)
                            ),  # todo
                        ),
                    ),
                    20,
                )
            except TimeoutError as e:
                raise HTTPException(
                    HTTP_500_INTERNAL_SERVER_ERROR,
                    "Error: timeout reading URLs and generating MosaicJSON definition",
                ) from e

            if mosaicjson is None:
                raise HTTPException(
                    HTTP_500_INTERNAL_SERVER_ERROR,
                    "Error: could not extract mosaic data",
                )

            mosaicjson.name = urisrb.name
            mosaicjson.description = urisrb.description
            mosaicjson.attribution = urisrb.attribution
            mosaicjson.version = urisrb.version if urisrb.version else "0.0.1"

            return mosaicjson
