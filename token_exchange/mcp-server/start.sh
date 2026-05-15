#!/bin/bash
# ATEX MCP Server entry point for npx
exec python3 "$(dirname "$0")/server.py" "$@"
