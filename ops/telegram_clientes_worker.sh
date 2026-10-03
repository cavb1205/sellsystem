#!/bin/sh

# Worker supervisado para avisos Telegram de clientes.
#
# El cron existente procesa la cola cada minuto. El cron horario de limpieza usa
# TELEGRAM_CLIENTES_LIMPIAR=1; ambos comparten el mismo bloqueo para evitar
# ejecuciones simultáneas. Las banderas de Django siguen siendo la última barrera.

set -eu

OPS_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
APP_DIR=$(CDPATH= cd -- "$OPS_DIR/.." && pwd)
if [ -n "${TELEGRAM_CLIENTES_PYTHON:-}" ]; then
    PYTHON_BIN=$TELEGRAM_CLIENTES_PYTHON
elif [ -x "$APP_DIR/venv/bin/python" ]; then
    PYTHON_BIN=$APP_DIR/venv/bin/python
elif [ -x "$APP_DIR/../venv/bin/python" ]; then
    PYTHON_BIN=$APP_DIR/../venv/bin/python
else
    PYTHON_BIN=$APP_DIR/venv/bin/python
fi
LOCK_FILE=${TELEGRAM_CLIENTES_LOCK:-/tmp/telegram_clientes_worker.lock}

if [ ! -x "$PYTHON_BIN" ]; then
    echo "No existe un Python ejecutable para Telegram: $PYTHON_BIN" >&2
    exit 1
fi

if ! command -v flock >/dev/null 2>&1; then
    echo "flock es obligatorio para evitar workers simultáneos" >&2
    exit 1
fi

cd "$APP_DIR"
if [ "${TELEGRAM_CLIENTES_LIMPIAR:-0}" = "1" ]; then
    set -- --limpiar --max-mensajes 500
elif [ -n "${TELEGRAM_CLIENTES_VINCULO_ID:-}" ]; then
    set -- --vinculo-id "$TELEGRAM_CLIENTES_VINCULO_ID" --max-eventos 1
else
    set -- --todos --max-eventos 20
fi
exec flock -n "$LOCK_FILE" "$PYTHON_BIN" manage.py telegram_clientes "$@"
