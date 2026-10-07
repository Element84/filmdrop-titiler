# Local development image; the Lambda package is built by build-lambda.sh
FROM python:3.13-slim

WORKDIR /app

# rasterio wheels need libexpat at runtime
RUN apt-get update \
    && apt-get install -y --no-install-recommends libexpat1 \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src

# boto3 is provided by the Lambda runtime, so it is not a project dependency
RUN pip install --no-cache-dir --group dev -e ".[server]" boto3

EXPOSE 8000

CMD ["uvicorn", "filmdrop_titiler.application.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "/app/src"]
