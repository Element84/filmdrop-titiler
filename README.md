# filmdrop-titiler

A lightweight FastAPI implementation of [TiTiler](https://github.com/developmentseed/titiler) for AWS Lambda deployment in the Element84 Filmdrop ecosystem.

This repository provides a simple Python package that generates an AWS Lambda ZIP distribution for deploying TiTiler as a serverless dynamic tile server. It uses a specific versioned release of TiTiler and is designed to be deployed via Terraform modules.

## Features

- **FastAPI-based**: Built on TiTiler's FastAPI framework for high performance
- **AWS Lambda Ready**: Optimized for serverless deployment with Mangum adapter
- **Lightweight**: Stripped-down implementation without Kubernetes or Azure components
- **Cloud Optimized GeoTIFF (COG) Support**: Serve tiles from COG files
- **STAC Support**: Work with SpatioTemporal Asset Catalog items
- **Versioned TiTiler**: Uses specific TiTiler version for reproducible deployments

## Installation

### For Local Development

```bash
# Clone the repository
git clone https://github.com/Element84/filmdrop-titiler.git
cd filmdrop-titiler

# Install with development dependencies
pip install -e ".[dev,server]"
```

### For Production (Lambda)

The Lambda deployment ZIP is automatically built and attached to GitHub releases. Download the `lambda-package.zip` from the [releases page](https://github.com/Element84/filmdrop-titiler/releases).

## Usage

### Running Locally

Start the development server:

```bash
uvicorn filmdrop_titiler.main:app --reload --port 8000
```

The API will be available at http://localhost:8000

Interactive API documentation is available at:
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc

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

## Deployment

### AWS Lambda

The package is designed to be deployed as an AWS Lambda function. The `dist/lambda-package.zip` file created by `make package` contains everything needed.

#### Lambda Configuration

- **Handler**: `filmdrop_titiler.handler.handler`
- **Runtime**: Python 3.9 or higher
- **Memory**: 1024 MB (minimum recommended)
- **Timeout**: 30 seconds (adjust based on your needs)

#### Environment Variables

- `TITILER_ROOT_PATH` - API root path (default: "")
- `TITILER_CORS_ORIGINS` - CORS origins (default: "*")
- `TITILER_CORS_ALLOW_METHODS` - CORS methods (default: "GET,POST")
- `TITILER_CACHECONTROL` - Cache control header (default: "public, max-age=3600")
- `TITILER_DEBUG` - Debug mode (default: false)

### Terraform Integration

This package is intended to be deployed via Terraform modules. The ZIP distribution can be referenced in your Terraform configuration:

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

```
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
pip install -e ".[dev,server]"

# Run tests
pytest
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

This project is similar to [Element84/titiler-mosaicjson](https://github.com/Element84/titiler-mosaicjson) but with key differences:

- **Simpler**: No mosaic/DynamoDB functionality
- **Lightweight**: No Kubernetes or Azure deployment options
- **Lambda-focused**: Optimized specifically for AWS Lambda deployment
- **Minimal**: Only core TiTiler functionality

## License

Apache-2.0

## Authors

Maintained by [Element 84](http://element84.com)

Based on [TiTiler](https://github.com/developmentseed/titiler) by [Development Seed](http://developmentseed.org)
