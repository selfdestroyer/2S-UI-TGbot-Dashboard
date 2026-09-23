#!/usr/bin/env bash
# ==============================================================================
# VPN Suite: Скрипт быстрого обновления сервисов из репозитория
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}[ОШИБКА] Скрипт должен быть запущен с правами root! (sudo bash $0)${NC}"
    exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/var/www/vpn-service"

echo -e "${CYAN}${BOLD}==> Запуск обновления VPN Suite...${NC}"

# Если репозиторий Git, получаем последние изменения
if [ -d "${SCRIPT_DIR}/.git" ]; then
    echo -e "${BLUE}[ИНФО] Получение изменений из Git...${NC}"
    cd "${SCRIPT_DIR}"
    git pull || echo -e "${YELLOW}[ВНИМАНИЕ] Не удалось выполнить git pull. Продолжаем с локальными файлами.${NC}"
fi

# Синхронизация файлов в рабочую папку
if [ -d "${INSTALL_DIR}" ]; then
    echo -e "${BLUE}[ИНФО] Синхронизация файлов в ${INSTALL_DIR}...${NC}"
    rsync -av --exclude 'venv' --exclude '__pycache__' --exclude '.env' --exclude '*.db' --exclude '.git' \
        "${SCRIPT_DIR}/" "${INSTALL_DIR}/"

    # Обновление зависимостей в общем виртуальном окружении
    echo -e "${BLUE}[ИНФО] Проверка и обновление библиотек Python...${NC}"
    "${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt" --quiet

    # Перезапуск служб
    echo -e "${BLUE}[ИНФО] Перезапуск systemd служб...${NC}"
    systemctl daemon-reload
    systemctl restart vpn-bot vpn-dashboard

    sleep 2

    BOT_ACTIVE=$(systemctl is-active vpn-bot || true)
    DASHBOARD_ACTIVE=$(systemctl is-active vpn-dashboard || true)

    echo ""
    echo -e "${BOLD}Статус служб:${NC}"
    echo -e "  • vpn-bot:       $([ "$BOT_ACTIVE" = "active" ] && echo -e "${GREEN}active${NC}" || echo -e "${RED}$BOT_ACTIVE${NC}")"
    echo -e "  • vpn-dashboard: $([ "$DASHBOARD_ACTIVE" = "active" ] && echo -e "${GREEN}active${NC}" || echo -e "${RED}$DASHBOARD_ACTIVE${NC}")"

    echo -e "\n${GREEN}${BOLD}Обновление успешно завершено!${NC}"
else
    echo -e "${RED}[ОШИБКА] Директория ${INSTALL_DIR} не найдена. Сначала выполните sudo bash install.sh${NC}"
    exit 1
fi
