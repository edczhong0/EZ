#!/bin/bash
cd "$(dirname "$0")"

# Load ANTHROPIC_API_KEY from .env file if it exists
if [ -f ".env" ]; then
  export $(grep -v '^#' .env | xargs)
fi

echo "Starting server at http://localhost:5001"
echo "Press Ctrl+C to stop."
echo ""

python3 server.py
