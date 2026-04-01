#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[INFO] install_picoclaw.sh устарел, используйте deploy.sh"
echo "[INFO] Передаю выполнение в deploy/deploy.sh"

bash "$ROOT_DIR/deploy/deploy.sh"
