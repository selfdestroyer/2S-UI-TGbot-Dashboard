#!/usr/bin/env bash
# Обновление теперь выполняет install.sh (git pull, бэкап, проверка и откат при сбое).
# Файл оставлен, чтобы старая команда sudo bash update.sh продолжала работать.
exec bash "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/install.sh" update "$@"
