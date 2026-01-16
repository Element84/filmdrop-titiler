from filmdrop_titiler.application.settings import ApiSettings
import json
from filmdrop_titiler.application.main import app
from titiler.core.middleware import LoggerMiddleware, TotalTimeMiddleware
from logging import config as log_config
import logging

logging.getLogger("botocore.credentials").disabled = True
logging.getLogger("botocore.utils").disabled = True
logging.getLogger("rasterio.session").setLevel(logging.ERROR)
logging.getLogger("rio-tiler").setLevel(logging.ERROR)

api_settings = ApiSettings()

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
