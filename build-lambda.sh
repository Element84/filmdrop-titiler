#!/bin/bash
set -e

# Build Lambda deployment package for filmdrop-titiler
# This script runs inside the AWS Lambda Python Docker container

echo "Installing system dependencies..."
dnf install -y zip binutils

echo "Installing Python dependencies..."
pip install --no-cache-dir --target lambda-package .

echo "Copying application source code..."
cp -r src/filmdrop_titiler lambda-package/

echo "Cleaning up to reduce package size..."
cd lambda-package

# Remove AWS SDK packages (Lambda already provides these)
echo "  - Removing boto packages..."
rm -rf boto* botocore* s3transfer*

# Remove test files and directories
echo "  - Removing test files..."
find . -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true
find . -type d -name "test" -exec rm -rf {} + 2>/dev/null || true

# Remove Python cache files
echo "  - Removing cache files..."
find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find . -name "*.pyc" -delete
find . -name "*.pyo" -delete

# Strip debug symbols from shared libraries to reduce size
echo "  - Stripping debug symbols from .so files..."
find . -name "*.so*" -exec strip {} \; 2>/dev/null || true

# Remove unnecessary metadata
echo "  - Cleaning up package metadata..."
find . -type f -name "RECORD" -delete 2>/dev/null || true

echo "Creating ZIP archive..."
zip -r9 ../$PACKAGE_NAME .

cd ..
echo "Lambda package created: $PACKAGE_NAME"
ls -lh $PACKAGE_NAME
