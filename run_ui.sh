#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$ROOT_DIR/.venv"
PORT="${PORT:-5050}"
WORKERS="${GUNICORN_WORKERS:-1}"

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "Создаю виртуальное окружение..."
  python3 -m venv "$VENV_DIR"
fi

echo "Устанавливаю зависимости..."
"$VENV_DIR/bin/python" -m pip install --upgrade pip
"$VENV_DIR/bin/python" -m pip install -r "$ROOT_DIR/requirements.txt"

exec "$VENV_DIR/bin/gunicorn" \
  --workers "$WORKERS" \
  --bind "127.0.0.1:$PORT" \
  --timeout 300 \
  --graceful-timeout 30 \
  "wsgi:app"

