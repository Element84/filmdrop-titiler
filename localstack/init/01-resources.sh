#!/bin/bash
set -euo pipefail

# Schema matches cogeo_mosaic's DynamoDBBackend
awslocal dynamodb create-table \
  --table-name mosaics \
  --attribute-definitions AttributeName=mosaicId,AttributeType=S AttributeName=quadkey,AttributeType=S \
  --key-schema AttributeName=mosaicId,KeyType=HASH AttributeName=quadkey,KeyType=RANGE \
  --billing-mode PAY_PER_REQUEST \
  --region us-east-1
