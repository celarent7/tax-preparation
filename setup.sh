#!/bin/bash
# Setup script for Tax Preparation Plugin

echo "Setting up Tax Preparation Plugin environment..."

# Check for Python 3
if ! command -v python3 &> /dev/null
then
    echo "Error: python3 could not be found. Please install Python 3.9 or higher."
    exit 1
fi

# Create virtual environment
echo "Creating virtual environment (.venv)..."
python3 -m venv .venv

# Activate virtual environment
source .venv/bin/activate

# Install dependencies
echo "Installing dependencies from requirements.txt..."
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "Setup complete!"
echo "To activate the environment, run: source .venv/bin/activate"
echo "To start the MCP server, run: python scripts/mcp_server.py"
