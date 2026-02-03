#!/bin/bash
set -e

# Build Lambda deployment package for filmdrop-titiler
# This script runs inside the AWS SAM build container

echo "Installing system dependencies..."
dnf install -y zip binutils findutils

echo "Installing Python dependencies..."
pip install --no-cache-dir --target lambda-package .

echo "Copying application source code..."
cp -r src/filmdrop_titiler lambda-package/

echo "Cleaning up to reduce package size..."
cd lambda-package

# Compile all Python files to bytecode first
echo "  - Compiling Python bytecode..."
python -m compileall -q .

# Compile Python files to .pyc and move them, then remove .py sources
# Preserve application source code (filmdrop_titiler) which needs .py files
echo "  - Optimizing Python files..."
find . -type f -name '*.pyc' | while read f; do n=$(echo $f | sed 's/__pycache__\///' | sed 's/.cpython-[0-9]*//'); cp $f $n; done
find . -type d -a -name '__pycache__' -print0 | xargs -0 rm -rf
find . -type f -a -name '*.py' -not -path "*/filmdrop_titiler/*" -print0 | xargs -0 rm -f

# Remove AWS SDK packages (Lambda already provides these)
echo "  - Removing boto packages..."
rm -rf boto* botocore* s3transfer*

# Remove test files and directories
echo "  - Removing test files..."
find . -type d -a -name 'tests' -print0 | xargs -0 rm -rf

# Remove heavy unnecessary directories
echo "  - Removing unnecessary directories..."
rm -rf numpy/doc/ bin/ geos_license Misc/

# Strip debug symbols from shared libraries to reduce size
# Exclude numpy.libs as stripping can corrupt pre-optimized BLAS/LAPACK libraries
echo "  - Stripping debug symbols from .so files..."
find . -type f -name '*.so*' -not -path "*/numpy.libs/*" -exec strip --strip-unneeded {} \; 2>/dev/null || true

cd ..

# Copy required system libraries after stripping to avoid them being stripped
echo "Copying required system libraries..."
cp /usr/lib64/libexpat.so.1 lambda-package/ 2>/dev/null || true

cd lambda-package

echo "Creating ZIP archive..."
zip -r9 ../$PACKAGE_NAME .

cd ..
echo "Lambda package created: $PACKAGE_NAME"
ls -lh $PACKAGE_NAME
