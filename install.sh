#!/usr/bin/env bash
# ==============================================================================
# VPN Suite: Telegram Bot + Web Dashboard (Единая директория)
# Скрипт автоматической установки на Ubuntu / Debian
# ==============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

log_info() {
    echo -e "${BLUE}[ИНФО]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[УСПЕХ]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[ВНИМАНИЕ]${NC} $1"
}

log_error() {
    echo -e "${RED}[ОШИБКА]${NC} $1"
}

print_banner() {
    clear 2>/dev/null || true
    echo -e "${CYAN}${BOLD}"
    echo "=================================================================="
    echo "      🚀 УСТАНОВЩИК VPN SUITE (VPN-BOT + VPN-DASHBOARD)          "
    echo "      Единая архитектура: одно окружение, независимые службы     "
    echo "=================================================================="
    echo -e "${NC}"
}

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/var/www/vpn-service"
CERTBOT_DIR="/var/www/certbot"

# Значения по умолчанию
MAIN_DOMAIN=""
SUB_DOMAIN=""
PROJECT_NAME="My VPN Service"
BOT_TOKEN=""
ADMIN_TG_ID=""
SUPPORT_BOT_USERNAME="my_vpn_support_bot"
SERVICE_GROUP_NAME="Официальный канал сервиса"
SERVICE_GROUP_URL="https://t.me/your_channel"
PAYMENT_REQUISITES="💳 <b>ПЕРЕВОД ПО НОМЕРУ КАРТЫ:</b>\n<code>0000 0000 0000 0000</code>\n<i>(нажмите на номер, чтобы скопировать)</i>"
DB_PATH="/usr/local/s-ui/db/s-ui.db"
PANEL_PORT="2096"
SETUP_SSL="y"
SSL_EMAIL=""
NON_INTERACTIVE=false

show_help() {
    echo "Использование: sudo bash install.sh [ОПЦИИ]"
    echo ""
    echo "Опции:"
    echo "  --main-domain <domain>   Основной домен сервера с панелью 2S-UI (например: example.com)"
    echo "  --domain <domain>        Поддомен для веб-дашборда и бота (например: sub.example.com)"
    echo "  --token <token>          Токен Telegram-бота от @BotFather"
    echo "  --admin <id>             ID администратора в Telegram"
    echo "  --project <name>         Название проекта (по умолчанию: 'My VPN Service')"
    echo "  --support <username>     Юзернейм саппорта Telegram (без @)"
    echo "  --db <path>              Путь к SQLite базе данных (по умолчанию: /usr/local/s-ui/db/s-ui.db)"
    echo "  --panel-port <port>      Порт панели 2S-UI/X-UI (по умолчанию: 2096)"
    echo "  --no-ssl                 Пропустить получение SSL Let's Encrypt"
    echo "  --ssl-email <email>      Email для регистрации SSL Let's Encrypt"
    echo "  -y, --non-interactive    Неинтерактивный режим"
    echo "  -h, --help               Показать данную справку"
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --main-domain) MAIN_DOMAIN="$2"; shift 2 ;;
        --domain|--subdomain) SUB_DOMAIN="$2"; shift 2 ;;
        --token) BOT_TOKEN="$2"; shift 2 ;;
        --admin) ADMIN_TG_ID="$2"; shift 2 ;;
        --project) PROJECT_NAME="$2"; shift 2 ;;
        --support) SUPPORT_BOT_USERNAME="$2"; shift 2 ;;
        --db) DB_PATH="$2"; shift 2 ;;
        --panel-port) PANEL_PORT="$2"; shift 2 ;;
        --no-ssl) SETUP_SSL="n"; shift ;;
        --ssl-email) SSL_EMAIL="$2"; shift 2 ;;
        -y|--non-interactive) NON_INTERACTIVE=true; shift ;;
        -h|--help) show_help ;;
        *) log_error "Неизвестный параметр: $1"; show_help ;;
    esac
done

check_prerequisites() {
    print_banner

    if [ "$EUID" -ne 0 ]; then
        log_error "Скрипт должен быть запущен с правами root!"
        echo "Запустите команду: sudo bash $0"
        exit 1
    fi

    if [ ! -f /etc/os-release ]; then
        log_error "Не удалось определить операционную систему."
        exit 1
    fi

    . /etc/os-release
    if [[ "$ID" != "ubuntu" && "$ID" != "debian" ]]; then
        log_warning "Данный скрипт оптимизирован для Ubuntu и Debian. Обнаружен: $ID."
        read -r -p "Продолжить установку? [y/N]: " confirm
        if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi

    log_success "Операционная система: $PRETTY_NAME"
}

run_wizard() {
    MAIN_DOMAIN="$(echo "$MAIN_DOMAIN" | sed -E 's~^https?://~~' | sed -E 's~/+$~~' | xargs)"
    SUB_DOMAIN="$(echo "$SUB_DOMAIN" | sed -E 's~^https?://~~' | sed -E 's~/+$~~' | xargs)"

    if [ "$NON_INTERACTIVE" = true ]; then
        if [ -z "$MAIN_DOMAIN" ] && [ -n "$SUB_DOMAIN" ]; then
            MAIN_DOMAIN="$(echo "$SUB_DOMAIN" | sed -E 's~^sub\.~~')"
        fi
        if [ -z "$SUB_DOMAIN" ] && [ -n "$MAIN_DOMAIN" ]; then
            SUB_DOMAIN="sub.${MAIN_DOMAIN}"
        fi
        if [ -z "$SUB_DOMAIN" ] || [ -z "$BOT_TOKEN" ] || [ -z "$ADMIN_TG_ID" ]; then
            log_error "В неинтерактивном режиме обязательно укажите --main-domain (или --domain), --token и --admin!"
            exit 1
        fi
        return
    fi

    echo -e "${BOLD}📋 Мастер первоначальной настройки${NC}\n"

    # 1. Основной домен сервера с панелью 2S-UI
    while [ -z "$MAIN_DOMAIN" ]; do
        echo -e "${YELLOW}1. Основной домен сервера, на котором работает панель 2S-UI:${NC}"
        echo -e "   (На этот домен направлены прямые ссылки на подписки панели: https://домен:${PANEL_PORT}/sub/...)"
        read -r -p "Основной домен [например example.com]: " input_main
        MAIN_DOMAIN="$(echo "$input_main" | sed -E 's~^https?://~~' | sed -E 's~/+$~~' | xargs)"
        if [ -z "$MAIN_DOMAIN" ]; then
            log_warning "Основной домен не может быть пустым!"
        fi
    done

    # 2. Поддомен для веб-дашборда и Telegram-бота
    if [ -z "$SUB_DOMAIN" ]; then
        default_sub="sub.${MAIN_DOMAIN}"
        echo -e "\n${YELLOW}2. Поддомен для веб-дашборда (на него бот выдает ссылки пользователям):${NC}"
        echo -e "   (A-запись этого поддомена должна указывать на IP этого сервера)"
        read -r -p "Поддомен [$default_sub]: " input_sub
        if [ -n "$input_sub" ]; then
            SUB_DOMAIN="$(echo "$input_sub" | sed -E 's~^https?://~~' | sed -E 's~/+$~~' | xargs)"
        else
            SUB_DOMAIN="$default_sub"
        fi
    fi
    SUB_DOMAIN="$(echo "$SUB_DOMAIN" | sed -E 's~^https?://~~' | sed -E 's~/+$~~' | xargs)"

    read -r -p "Порт панели 2S-UI для выдачи подписок [$PANEL_PORT]: " input_panel_port
    [ -n "$input_panel_port" ] && PANEL_PORT="$input_panel_port"

    read -r -p "Название VPN сервиса [$PROJECT_NAME]: " input_project
    [ -n "$input_project" ] && PROJECT_NAME="$input_project"

    while [ -z "$BOT_TOKEN" ]; do
        echo -e "\n${YELLOW}Введите Telegram Bot Token (от @BotFather):${NC}"
        read -r -p "BOT_TOKEN: " input_token
        BOT_TOKEN="$(echo "$input_token" | xargs)"
    done

    while [ -z "$ADMIN_TG_ID" ]; do
        echo -e "\n${YELLOW}Введите ваш цифровой Telegram ID (от @userinfobot):${NC}"
        read -r -p "ADMIN_TG_ID: " input_admin
        ADMIN_TG_ID="$(echo "$input_admin" | tr -cd '0-9')"
    done

    read -r -p "Юзернейм саппорта в Telegram без @ [$SUPPORT_BOT_USERNAME]: " input_support
    [ -n "$input_support" ] && SUPPORT_BOT_USERNAME="${input_support#@}"

    read -r -p "Название инфо-канала [$SERVICE_GROUP_NAME]: " input_chan_name
    [ -n "$input_chan_name" ] && SERVICE_GROUP_NAME="$input_chan_name"

    read -r -p "Ссылка на инфо-канал [$SERVICE_GROUP_URL]: " input_chan_url
    [ -n "$input_chan_url" ] && SERVICE_GROUP_URL="$input_chan_url"

    echo -e "\n${YELLOW}Путь к SQLite базе данных панели 2S-UI / X-UI:${NC}"
    read -r -p "DB_PATH [$DB_PATH]: " input_db
    [ -n "$input_db" ] && DB_PATH="$input_db"

    if [ ! -f "$DB_PATH" ]; then
        log_warning "Файл базы данных '$DB_PATH' на данный момент не найден."
        log_warning "Сервисы будут настроены, но для их работы потребуется наличие этой БД."
    fi

    echo -e "\n${YELLOW}Настроить бесплатный SSL Let's Encrypt через Certbot?${NC}"
    read -r -p "[Y/n]: " input_ssl
    if [[ "$input_ssl" =~ ^[Nn]$ ]]; then
        SETUP_SSL="n"
    else
        SETUP_SSL="y"
        read -r -p "Email для уведомлений Let's Encrypt (можно оставить пустым): " input_email
        SSL_EMAIL="$input_email"
    fi

    echo ""
    echo -e "${CYAN}------------------------------------------------------------------${NC}"
    echo -e "${BOLD}Параметры установки:${NC}"
    echo "  Целевая папка:                  $INSTALL_DIR"
    echo "  Основной домен (панель 2S-UI):  $MAIN_DOMAIN (порт $PANEL_PORT)"
    echo "  Поддомен дашборда (для бота):   $SUB_DOMAIN"
    echo "  Проект:                         $PROJECT_NAME"
    echo "  Admin ID:                       $ADMIN_TG_ID"
    echo "  Саппорт:                        @$SUPPORT_BOT_USERNAME"
    echo "  База 2S-UI:                     $DB_PATH"
    echo "  Настройка SSL:                  $SETUP_SSL"
    echo -e "${CYAN}------------------------------------------------------------------${NC}"
    read -r -p "Начать установку? [Y/n]: " proceed
    if [[ "$proceed" =~ ^[Nn]$ ]]; then
        echo "Установка отменена."
        exit 0
    fi
}

install_system_packages() {
    log_info "Установка системных пакетов..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y -q
    apt-get install -y -q \
        python3 \
        python3-pip \
        python3-venv \
        git \
        curl \
        nginx \
        certbot \
        python3-certbot-nginx \
        sqlite3 \
        rsync
    log_success "Системные пакеты установлены."
}

deploy_project_files() {
    log_info "Копирование файлов проекта в ${INSTALL_DIR}..."

    mkdir -p "${INSTALL_DIR}"
    rsync -av --exclude 'venv' --exclude '__pycache__' --exclude '.env' --exclude '*.db' --exclude '.git' \
        "${SCRIPT_DIR}/" "${INSTALL_DIR}/"

    # Кастомизация фронтенда
    if [ -f "${INSTALL_DIR}/index.html" ]; then
        log_info "Адаптация веб-интерфейса index.html под домен и проект..."
        sed -i "s/window\.PANEL_DOMAIN = window\.PANEL_DOMAIN || \".*\"/window.PANEL_DOMAIN = \"${MAIN_DOMAIN}\"/g" "${INSTALL_DIR}/index.html"
        sed -i "s/window\.PANEL_PORT = window\.PANEL_PORT || \".*\"/window.PANEL_PORT = \"${PANEL_PORT}\"/g" "${INSTALL_DIR}/index.html"
        sed -i "s/podnyatie\.space/${MAIN_DOMAIN}/g" "${INSTALL_DIR}/index.html"
        sed -i "s/podnyatie_support_bot/${SUPPORT_BOT_USERNAME}/g" "${INSTALL_DIR}/index.html"
        sed -i "s/podnyatie_vpn_bot/${SUPPORT_BOT_USERNAME}/g" "${INSTALL_DIR}/index.html"
    fi

    # Создание общего виртуального окружения Python
    if [ ! -d "${INSTALL_DIR}/venv" ]; then
        log_info "Создание единого виртуального окружения Python..."
        python3 -m venv "${INSTALL_DIR}/venv"
    fi

    log_info "Установка библиотек из requirements.txt..."
    "${INSTALL_DIR}/venv/bin/pip" install --upgrade pip --quiet
    "${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt" --quiet

    # Создание единого файла конфигурации .env
    log_info "Создание конфигурации ${INSTALL_DIR}/.env..."
    cat > "${INSTALL_DIR}/.env" <<EOF
# Единая конфигурация VPN Suite (сгенерировано инсталятором)
BOT_TOKEN=${BOT_TOKEN}
ADMIN_TG_ID=${ADMIN_TG_ID}
DB_PATH=${DB_PATH}
BOT_DATA_DB=${INSTALL_DIR}/bot_data.db
PROJECT_NAME=${PROJECT_NAME}

# Основной домен сервера с панелью 2S-UI и порт для сырых подписок
MAIN_DOMAIN=${MAIN_DOMAIN}
PANEL_PORT=${PANEL_PORT}

# Поддомен веб-дашборда (ссылки, которые бот отправляет клиентам: https://{SUB_DOMAIN}/{client_name})
SUB_DOMAIN=${SUB_DOMAIN}

SUPPORT_BOT_USERNAME=${SUPPORT_BOT_USERNAME}
SERVICE_GROUP_NAME=${SERVICE_GROUP_NAME}
SERVICE_GROUP_URL=${SERVICE_GROUP_URL}
PAYMENT_REQUISITES="${PAYMENT_REQUISITES}"
PORT=8000
EOF
    chmod 600 "${INSTALL_DIR}/.env"

    # Регистрация systemd служб
    log_info "Установка systemd служб..."
    cp "${INSTALL_DIR}/vpn-bot.service" /etc/systemd/system/vpn-bot.service
    cp "${INSTALL_DIR}/vpn-dashboard.service" /etc/systemd/system/vpn-dashboard.service

    log_success "Файлы и службы успешно подготовлены."
}

setup_nginx_and_ssl() {
    log_info "Настройка веб-сервера Nginx..."

    mkdir -p "${CERTBOT_DIR}"
    NGINX_CONF="/etc/nginx/sites-available/vpn-sub.conf"

    cat > "$NGINX_CONF" <<EOF
server {
    listen 80;
    server_name ${SUB_DOMAIN};

    location /.well-known/acme-challenge/ {
        root ${CERTBOT_DIR};
    }

    # Веб-дашборд и валидация
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # Проксирование запросов к панели 2S-UI
    location /sub/ {
        proxy_pass https://127.0.0.1:${PANEL_PORT}/;
        proxy_ssl_verify off;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
EOF

    ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/vpn-sub.conf

    if [ -L /etc/nginx/sites-enabled/default ]; then
        rm -f /etc/nginx/sites-enabled/default
    fi

    nginx -t
    systemctl restart nginx

    if [ "$SETUP_SSL" = "y" ]; then
        log_info "Получение SSL-сертификата Let's Encrypt для ${SUB_DOMAIN}..."
        
        CERTBOT_EMAIL_CMD="--register-unsafely-without-email"
        if [ -n "$SSL_EMAIL" ]; then
            CERTBOT_EMAIL_CMD="-m ${SSL_EMAIL}"
        fi

        set +e
        certbot --nginx -d "${SUB_DOMAIN}" --non-interactive --agree-tos ${CERTBOT_EMAIL_CMD} --redirect
        CERTBOT_RES=$?
        set -e

        if [ $CERTBOT_RES -eq 0 ]; then
            log_success "SSL Let's Encrypt успешно получен и применен!"
            systemctl reload nginx
        else
            log_warning "Не удалось получить SSL (возможно DNS домена еще не обновился)."
            log_warning "Сайт пока доступен по обычному HTTP (порт 80)."
            echo -e "${CYAN}Для получения сертификата позже запустите:${NC}"
            echo -e "  sudo certbot --nginx -d ${SUB_DOMAIN}\n"
        fi
    fi
}

start_and_verify_services() {
    log_info "Запуск служб systemd..."

    systemctl daemon-reload
    systemctl enable vpn-bot vpn-dashboard
    systemctl restart vpn-bot vpn-dashboard

    sleep 2

    BOT_ACTIVE=$(systemctl is-active vpn-bot || true)
    DASHBOARD_ACTIVE=$(systemctl is-active vpn-dashboard || true)
    NGINX_ACTIVE=$(systemctl is-active nginx || true)

    echo ""
    echo -e "${BOLD}🔍 Результаты проверки служб:${NC}"

    if [ "$BOT_ACTIVE" = "active" ]; then
        echo -e "  [✅] vpn-bot:             ${GREEN}РАБОТАЕТ (Active)${NC}"
    else
        echo -e "  [❌] vpn-bot:             ${RED}ОШИБКА ($BOT_ACTIVE)${NC}"
        echo "       Логи: journalctl -u vpn-bot -n 30 --no-pager"
    fi

    if [ "$DASHBOARD_ACTIVE" = "active" ]; then
        echo -e "  [✅] vpn-dashboard:       ${GREEN}РАБОТАЕТ (Active)${NC}"
    else
        echo -e "  [❌] vpn-dashboard:       ${RED}ОШИБКА ($DASHBOARD_ACTIVE)${NC}"
        echo "       Логи: journalctl -u vpn-dashboard -n 30 --no-pager"
    fi

    if [ "$NGINX_ACTIVE" = "active" ]; then
        echo -e "  [✅] Nginx:               ${GREEN}РАБОТАЕТ (Active)${NC}"
    else
        echo -e "  [❌] Nginx:               ${RED}ОШИБКА ($NGINX_ACTIVE)${NC}"
    fi

    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/ || true)
    if [ "$HTTP_CODE" = "200" ] || [ "$HTTP_CODE" = "404" ]; then
        echo -e "  [✅] FastAPI API:         ${GREEN}ОТВЕЧАЕТ (HTTP $HTTP_CODE)${NC}"
    fi
}

print_summary() {
    PROTOCOL="http"
    if [ -f "/etc/letsencrypt/live/${SUB_DOMAIN}/fullchain.pem" ]; then
        PROTOCOL="https"
    fi

    echo ""
    echo -e "${GREEN}${BOLD}==================================================================${NC}"
    echo -e "${GREEN}${BOLD}             🎉 УСТАНОВКА УСПЕШНО ЗАВЕРШЕНА!                     ${NC}"
    echo -e "${GREEN}${BOLD}==================================================================${NC}"
    echo ""
    echo -e "🌐 ${BOLD}Ссылки сервиса:${NC}"
    echo -e "  • Ссылка от бота (веб-дашборд):     ${CYAN}${PROTOCOL}://${SUB_DOMAIN}/{client_name}${NC}"
    echo -e "  • Ссылка на подписку (из панели):   ${CYAN}https://${MAIN_DOMAIN}:${PANEL_PORT}/sub/{client_name}${NC}"
    echo -e "  • Telegram саппорт:                 ${CYAN}@${SUPPORT_BOT_USERNAME}${NC}"
    echo ""
    echo -e "🛠 ${BOLD}Управление сервисами:${NC}"
    echo -e "  • Статус бота:              ${YELLOW}systemctl status vpn-bot${NC}"
    echo -e "  • Статус дашборда:          ${YELLOW}systemctl status vpn-dashboard${NC}"
    echo -e "  • Логи бота в реал-тайме:   ${YELLOW}journalctl -u vpn-bot -f${NC}"
    echo -e "  • Логи дашборда в реал-тайме: ${YELLOW}journalctl -u vpn-dashboard -f${NC}"
    echo -e "  • Файл конфигурации:        ${YELLOW}nano ${INSTALL_DIR}/.env${NC}"
    echo ""
    echo -e "🔄 ${BOLD}Обновление из репозитория:${NC}"
    echo -e "  ${YELLOW}sudo bash ${SCRIPT_DIR}/update.sh${NC}"
    echo ""
    echo -e "${GREEN}==================================================================${NC}\n"
}

main() {
    check_prerequisites
    run_wizard
    install_system_packages
    deploy_project_files
    setup_nginx_and_ssl
    start_and_verify_services
    print_summary
}

main "$@"
