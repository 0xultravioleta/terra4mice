#!/usr/bin/env bash
# Publish terra4mice to PyPI
#
# Prerequisites:
#   pip install build twine
#   Set TWINE_USERNAME and TWINE_PASSWORD or use .pypirc
#
# Usage:
#   ./scripts/publish.sh        # Publish to PyPI
#   ./scripts/publish.sh test   # Publish to TestPyPI first

set -euo pipefail

cd "$(dirname "$0")/.."

echo "🧹 Cleaning previous builds..."
rm -rf dist/ build/ src/*.egg-info

echo "📦 Building package..."
python3 -m build

echo "🔍 Checking package..."
python3 -m twine check dist/*

if [[ "${1:-}" == "test" ]]; then
    echo "🧪 Uploading to TestPyPI..."
    python3 -m twine upload --repository testpypi dist/*
    echo ""
    echo "✅ Published to TestPyPI!"
    echo "   pip install --index-url https://test.pypi.org/simple/ terra4mice"
else
    echo "🚀 Uploading to PyPI..."
    python3 -m twine upload dist/*
    echo ""
    echo "✅ Published to PyPI!"
    echo "   pip install terra4mice"
fi
