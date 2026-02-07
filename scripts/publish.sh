#!/bin/bash
# Publish terra4mice to PyPI
# 
# Prerequisites:
#   pip install build twine
#   PyPI API token in ~/.pypirc or TWINE_PASSWORD env var
#
# Usage:
#   ./scripts/publish.sh        # Publish to PyPI
#   ./scripts/publish.sh test   # Publish to TestPyPI first

set -e

cd "$(dirname "$0")/.."

echo "🐭 Terra4mice Publisher"
echo "======================"

# Check prerequisites
command -v python3 >/dev/null 2>&1 || { echo "❌ python3 required"; exit 1; }

# Ensure build tools
python3 -m pip install --upgrade build twine --quiet

# Clean previous builds
echo "🧹 Cleaning previous builds..."
rm -rf dist/ build/ *.egg-info src/*.egg-info

# Run tests first
echo "🧪 Running tests..."
python3 -m pytest tests/ -q --tb=short || { echo "❌ Tests failed"; exit 1; }

# Build
echo "📦 Building package..."
python3 -m build

# Show what we built
echo "📋 Built packages:"
ls -la dist/

# Check the package
echo "🔍 Checking package..."
python3 -m twine check dist/*

# Publish
if [ "$1" = "test" ]; then
    echo "🚀 Publishing to TestPyPI..."
    python3 -m twine upload --repository testpypi dist/*
    echo "✅ Published to TestPyPI!"
    echo "   Install: pip install -i https://test.pypi.org/simple/ terra4mice"
else
    echo "🚀 Publishing to PyPI..."
    python3 -m twine upload dist/*
    echo "✅ Published to PyPI!"
    echo "   Install: pip install terra4mice"
fi

echo ""
echo "🎉 Done!"
