"""AWS Lambda handler."""

import logging

from mangum import Mangum

from filmdrop_titiler.main import app

logging.getLogger("mangum.lifespan").setLevel(logging.ERROR)
logging.getLogger("mangum.http").setLevel(logging.ERROR)


def handler(event, context):
    """AWS Lambda handler function."""
    asgi_handler = Mangum(app, lifespan="auto")
    return asgi_handler(event, context)
