#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if command -v uv >/dev/null 2>&1; then
  uv sync --extra rerank >/dev/null
  exec uv run paperdeck serve --open "$@"
fi

if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
. .venv/bin/activate
pip install --quiet -e ".[rerank]"
exec paperdeck serve --open "$@"
