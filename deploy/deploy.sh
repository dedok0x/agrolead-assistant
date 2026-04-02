#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DEPLOY="$SCRIPT_DIR/../deploy.sh"

if [[ ! -f "$ROOT_DEPLOY" ]]; then
  echo "deploy.sh не найден в корне проекта" >&2
  exit 1
fi

exec bash "$ROOT_DEPLOY" "$@"
