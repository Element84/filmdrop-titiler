# filmdrop-titiler

A lightweight FastAPI implementation of
[TiTiler](https://github.com/developmentseed/titiler) for AWS Lambda
deployment in the Element84 Filmdrop ecosystem, generated with AI Assistance.

This repository provides a simple Python package that generates an AWS Lambda ZIP
distribution for deploying TiTiler as a serverless dynamic tile server.
It uses a specific versioned release of TiTiler and is designed to be
 deployed via Terraform modules.

## Features

- **FastAPI-based**: Built on TiTiler's FastAPI framework for high performance
- **AWS Lambda Ready**: Optimized for serverless deployment with Mangum adapter
- **Cloud Optimized GeoTIFF (COG) Support**: Serve tiles from COG files
- **STAC Support**: Work with SpatioTemporal Asset Catalog items
- **Versioned TiTiler**: Uses specific TiTiler version for reproducible deployments

## Installation

### For Local Development

```bash
# Clone the repository
git clone https://github.com/Element84/filmdrop-titiler.git
cd filmdrop-titiler

# Install with development dependencies (uv includes the dev group by default)
uv sync --extra server

# or with pip (25.1+)
pip install --group dev -e ".[server]"
```

### For Production (Lambda)

The Lambda deployment ZIP is automatically built and attached to GitHub
releases. The `release.yml` workflow packages the app into a lambda
dist that can be downloaded as `lambda-package.zip` from
the [releases page](https://github.com/Element84/filmdrop-titiler/releases).

## Usage

### Running Locally

Navigate your shell to the `src` directory and start the development server:

```bash
uvicorn filmdrop_titiler.main:app --reload --port 8000
```

The API will be available at [http://localhost:8000](http://localhost:8000)

Interactive API documentation is available at:

- Swagger UI: [http://localhost:8000/docs](http://localhost:8000/docs)
- ReDoc: [http://localhost:8000/redoc](http://localhost:8000/redoc)

### API Endpoints

The following endpoints are available:

- `/cog` - Cloud Optimized GeoTIFF tiler endpoints
- `/stac` - STAC item tiler endpoints
- `/algorithms` - Image processing algorithms
- `/tms` - TileMatrixSet endpoints
- `/healthz` - Health check endpoint

### Example Requests

**Get COG info:**

```bash
curl "http://localhost:8000/cog/info?url=https://example.com/image.tif"
```

**Get a tile:**

```bash
curl "http://localhost:8000/cog/tiles/14/3241/6253?url=https://example.com/image.tif"
```

### Local Development with Docker Compose

`docker-compose.yml` runs the app together with
[LocalStack](https://github.com/localstack/localstack) (DynamoDB and S3), with
the mosaic endpoints backed by DynamoDB, as in a deployed environment.

```bash
docker compose up -d --build --wait
curl http://localhost:8000/healthz
```

- App: <http://localhost:8000> (`./src` is mounted and the server auto-reloads)
- LocalStack: <http://localhost:4566>
- On startup, `localstack/init/01-resources.sh` creates the DynamoDB table `mosaics`.
- LocalStack is pinned to `4.14.0` because newer images require a LocalStack
  auth token.

Create a mosaic from a STAC API query and fetch its TileJSON:

```bash
curl -X POST http://localhost:8000/mosaicjson/mosaics \
  -H 'content-type: application/vnd.titiler.stac-api-query+json' \
  -d '{
    "stac_api_root": "https://earth-search.aws.element84.com/v2",
    "collections": ["sentinel-2-l2a"],
    "asset_name": "visual",
    "bbox": [-105.3, 39.9, -105.1, 40.1],
    "datetime": "2026-07-01T00:00:00Z/2026-07-30T23:59:59Z",
    "max_items": 20,
    "query": {"eo:cloud_cover": {"lt": 20}}
  }'

curl http://localhost:8000/mosaicjson/mosaics/<id>/tilejson.json
curl -o tile.png http://localhost:8000/mosaicjson/mosaics/<id>/tiles/10/212/388.png
```

Notes:

- Tiles over areas with no imagery return `204 No Content`. The first tile of a
  large mosaic can take a while to render.
- The mosaic items are written to LocalStack DynamoDB; inspect them with
  `docker compose exec localstack awslocal dynamodb scan
  --table-name mosaics --region us-east-1`.
- The app reads public `s3://` COGs unsigned (`AWS_NO_SIGN_REQUEST=YES`). To read
  from LocalStack S3 instead, see the comment in `docker-compose.yml`.
- `boto3` is installed in the dev image only; the Lambda runtime provides it.
- To run the tests in the container:
  `docker compose run --rm app pytest src/filmdrop_titiler/tests`.
- Stop everything with `docker compose down`.

## Deployment

### AWS Lambda

The package is designed to be deployed as an AWS Lambda function. The
`dist/lambda-package.zip` file created by releases contains everything needed.

#### Lambda Configuration

- **Handler**: `filmdrop_titiler.handler.handler`
- **Runtime**: Python 3.13 or higher
- **Memory**: 1024 MB (minimum recommended)
- **Timeout**: 30 seconds (adjust based on your needs)

#### Environment Variables

- `TITILER_ROOT_PATH` - API root path (default: "")
- `TITILER_CORS_ORIGINS` - CORS origins (default: "*")
- `TITILER_CORS_ALLOW_METHODS` - CORS methods (default: "GET,POST")
- `TITILER_CACHECONTROL` - Cache control header (default: "public, max-age=3600")
- `TITILER_DEBUG` - Debug mode (default: false)

### Terraform Integration

This package is intended to be deployed via Terraform modules. The
ZIP distribution can be referenced in your Terraform configuration:

```hcl
resource "aws_lambda_function" "titiler" {
  filename         = "path/to/lambda-package.zip"
  function_name    = "filmdrop-titiler"
  role            = aws_iam_role.lambda_role.arn
  handler         = "filmdrop_titiler.handler.handler"
  runtime         = "python3.9"
  timeout         = 30
  memory_size     = 1024

  environment {
    variables = {
      TITILER_ROOT_PATH = "/tiles"
    }
  }
}
```

## Development

### Project Structure

```text
filmdrop-titiler/
├── filmdrop_titiler/
│   ├── __init__.py      # Package version
│   ├── main.py          # FastAPI application
│   ├── settings.py      # Configuration settings
│   └── handler.py       # AWS Lambda handler
├── pyproject.toml       # Project metadata and dependencies
└── README.md           # This file
```

### Testing

```bash
# Install development dependencies
uv sync --extra server

# Run tests
uv run pytest
```

### Creating a Release

When a new release is created on GitHub, the CI/CD workflow automatically:

1. Builds the Lambda deployment package
2. Attaches the `lambda-package.zip` to the release

To create a release:

1. Tag the commit: `git tag v0.1.0`
2. Push the tag: `git push origin v0.1.0`
3. Create a release on GitHub using the tag
4. The workflow will build and attach the Lambda ZIP

## Differences from titiler-mosaicjson

This project is similar to
[Element84/titiler-mosaicjson](https://github.com/Element84/titiler-mosaicjson)
 but with key differences:

- **Lightweight**: No Kubernetes or Azure deployment options
- **Lambda-focused**: Optimized specifically for AWS Lambda deployment

## License

Apache-2.0

## Authors

Maintained by [Element 84](http://element84.com)

Based on [TiTiler](https://github.com/developmentseed/titiler) by [Development Seed](http://developmentseed.org)

Readme generated with AI Assistance
