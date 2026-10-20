#!/usr/bin/env bash
# ==============================================================================
# Создаёт системного пользователя для vpn-bot / vpn-dashboard.
# Права на файлы не меняются: доступ даёт CAP_DAC_OVERRIDE в unit-файлах,
# а запись ограничена песочницей systemd (ProtectSystem=strict + ReadWritePaths).
# Вызывается из install.sh и update.sh, можно запускать повторно.
# ==============================================================================

set -euo pipefail

SERVICE_USER="vpnbot"

if ! id -u "${SERVICE_USER}" >/dev/null 2>&1; then
    useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "${SERVICE_USER}"
    echo "[ИНФО] Создан системный пользователь ${SERVICE_USER}"
fi
