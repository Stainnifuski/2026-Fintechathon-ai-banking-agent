#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
test -f .env || { echo 'Missing .env: copy .env.example once and configure your own secrets.' >&2; exit 1; }
mkdir -p .deps/wealth_portfolio_data/snapshots .deps/upgrade_backups
export SOURCE_REVISION=source-archive
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  SOURCE_REVISION=$(git rev-parse --short HEAD)
  if test -n "$(git status --porcelain --untracked-files=no)"; then SOURCE_REVISION+=-dirty; fi
fi
compose=(docker compose -f docker-compose.yml)
if test -f .deps/bank-runtime.override.yml; then compose+=(-f .deps/bank-runtime.override.yml); fi
"${compose[@]}" config --quiet
"${compose[@]}" build backend frontend
if test "${1:-}" != --skip-voice; then
  if "${compose[@]}" build sensevoice; then
    "${compose[@]}" up -d --no-deps sensevoice || echo 'Voice unavailable; text input remains available.' >&2
  else
    echo 'Voice build failed; continuing bank startup. Check network access and retry voice.' >&2
  fi
fi
"${compose[@]}" up -d --wait --wait-timeout 300 backend
"${compose[@]}" up -d --no-deps --force-recreate --wait --wait-timeout 60 frontend
echo 'Ready: http://localhost:3000 . Voice readiness is shown in NOVA; first run downloads the model.'
