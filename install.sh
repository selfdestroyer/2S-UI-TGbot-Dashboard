#!/usr/bin/env bash
# ==============================================================================
# 2S-UI-TGbot-Dashboard: установщик Telegram-бота и веб-дашборда для 2S-UI
#
#   sudo bash install.sh [install]     установка или переустановка (мастер)
#   sudo bash install.sh reconfigure   изменить настройки без переустановки пакетов
#   sudo bash install.sh update        обновить код из Git с откатом при сбое
#   sudo bash install.sh status        проверить, что всё работает
#   sudo bash install.sh uninstall     удалить (с бэкапом настроек и базы бота)
#
# Установка одной командой:
#   bash <(curl -fsSL https://raw.githubusercontent.com/selfdestroyer/2S-UI-TGbot-Dashboard/main/install.sh)
# ==============================================================================

set -euo pipefail

REPO_URL="https://github.com/selfdestroyer/2S-UI-TGbot-Dashboard.git"
CLONE_DIR="/root/2S-UI-TGbot-Dashboard"
INSTALL_DIR="/var/www/2S-UI-TGbot-Dashboard"
ENV_FILE="${INSTALL_DIR}/.env"
SERVICE_USER="vpnbot"
NGINX_CONF="/etc/nginx/sites-available/vpn-sub.conf"
NGINX_LINK="/etc/nginx/sites-enabled/vpn-sub.conf"
CERTBOT_DIR="/var/www/certbot"
RENEW_HOOK="/etc/letsencrypt/renewal-hooks/deploy/vpn-sub-reload-nginx.sh"
BACKUP_CRON="/etc/cron.daily/vpn-bot-backup"
BACKUP_DIR="/root/2s-ui-tgbot-backups"
LOG_FILE="/var/log/2s-ui-tgbot-install.log"
DEFAULT_DB_PATH="/usr/local/s-ui/db/s-ui.db"
DEFAULT_TARIFFS="1:200,3:570:5,6:1080:10,12:2000:15"
REQUIRED_COLUMNS="name desc remark tg_id expiry enable auto_reset next_reset volume up down"

# Ключи .env, которыми управляет установщик (остальные ключи файла не трогаются)
ENV_KEYS=(BOT_TOKEN ADMIN_TG_ID BOT_USERNAME PROJECT_NAME DB_PATH BOT_DATA_DB
          MAIN_DOMAIN PANEL_PORT SUB_DOMAIN SUPPORT_BOT_USERNAME
          SERVICE_GROUP_NAME SERVICE_GROUP_URL CARD_NUMBER PAYMENT_REQUISITES
          TARIFFS TIMEZONE PANEL_SUB_URL)

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

log_info()    { echo -e "${BLUE}[ИНФО]${NC} $1"; }
log_success() { echo -e "${GREEN}[УСПЕХ]${NC} $1"; }
log_warning() { echo -e "${YELLOW}[ВНИМАНИЕ]${NC} $1"; }
log_error()   { echo -e "${RED}[ОШИБКА]${NC} $1" >&2; }
die()         { log_error "$1"; exit 1; }
section()     { echo -e "\n${CYAN}${BOLD}── $1${NC}"; }

trim() {
    local s="$1"
    s="${s#"${s%%[![:space:]]*}"}"
    s="${s%"${s##*[![:space:]]}"}"
    printf '%s' "$s"
}

# ----------------------------- Аргументы --------------------------------------

MODE="install"
NON_INTERACTIVE=false
PULLED=false
SETUP_SSL=""
SSL_EMAIL=""
HTTPS_PORT=""
CONFIG_FILE=""
declare -A CLI=()

show_help() {
    cat <<EOF
Использование: sudo bash install.sh [РЕЖИМ] [ОПЦИИ]

Режимы:
  install        Установка или переустановка (по умолчанию)
  reconfigure    Изменить настройки (мастер с текущими значениями)
  update         Обновить код из Git, при сбое откатиться
  status         Проверить службы, базу, сайт и сертификат
  uninstall      Удалить службы и сайт (настройки и база бота сохраняются в бэкап)

Опции (для install/reconfigure, всё можно ввести и в мастере):
  --main-domain <domain>   Домен панели 2S-UI (по умолчанию берётся из настроек панели)
  --domain <domain>        Поддомен дашборда, например sub.example.com
  --admin <id>             Telegram ID администратора
  --token <token>          Токен бота (лучше передать через переменную BOT_TOKEN
                           или ввести в мастере: аргументы видны в истории и ps)
  --project <name>         Название сервиса
  --support <username>     Юзернейм поддержки без @
  --group-name <name>      Название инфо-канала
  --group-url <url>        Ссылка на инфо-канал
  --card <number>          Номер карты для оплаты
  --requisites <text>      Свой текст реквизитов (HTML, перевод строки — \\n)
  --tariffs <list>         Тарифы: месяцы:цена[:скидка%],... (по умолчанию ${DEFAULT_TARIFFS})
  --db <path>              Путь к s-ui.db (по умолчанию ищется автоматически)
  --panel-port <port>      Порт подписок панели (по умолчанию из настроек панели)
  --https-port <port>      Порт HTTPS для дашборда, если 443 занят
  --tz <zone>              Часовой пояс для дат в уведомлениях, например Europe/Moscow
  --no-ssl                 Не получать сертификат Let's Encrypt
  --ssl-email <email>      Email для Let's Encrypt
  --config <file>          Взять значения из файла в формате .env
  -y, --non-interactive    Без вопросов (нужны --domain/--main-domain, --admin и токен)
  -h, --help               Эта справка
EOF
    exit 0
}

parse_args() {
    while [[ $# -gt 0 ]]; do
        case "$1" in
            install|reconfigure|update|status|uninstall) MODE="$1"; shift ;;
            --main-domain) CLI[MAIN_DOMAIN]="$2"; shift 2 ;;
            --domain|--subdomain) CLI[SUB_DOMAIN]="$2"; shift 2 ;;
            --token) CLI[BOT_TOKEN]="$2"; TOKEN_FROM_ARGS=true; shift 2 ;;
            --admin) CLI[ADMIN_TG_ID]="$2"; shift 2 ;;
            --project) CLI[PROJECT_NAME]="$2"; shift 2 ;;
            --bot-username) shift 2 ;;  # определяется по токену, оставлено для совместимости
            --support) CLI[SUPPORT_BOT_USERNAME]="${2#@}"; shift 2 ;;
            --group-name) CLI[SERVICE_GROUP_NAME]="$2"; shift 2 ;;
            --group-url) CLI[SERVICE_GROUP_URL]="$2"; shift 2 ;;
            --card|--card-number) CLI[CARD_NUMBER]="$2"; CLI_PAYMENT_MODE="card"; shift 2 ;;
            --requisites) CLI[PAYMENT_REQUISITES]="$2"; CLI_PAYMENT_MODE="custom"; shift 2 ;;
            --tariffs) CLI[TARIFFS]="$2"; shift 2 ;;
            --db) CLI[DB_PATH]="$2"; shift 2 ;;
            --panel-port) CLI[PANEL_PORT]="$2"; shift 2 ;;
            --https-port) HTTPS_PORT="$2"; shift 2 ;;
            --tz) CLI[TIMEZONE]="$2"; shift 2 ;;
            --no-ssl) SETUP_SSL="n"; shift ;;
            --ssl-email) SSL_EMAIL="$2"; shift 2 ;;
            --config) CONFIG_FILE="$2"; shift 2 ;;
            -y|--non-interactive) NON_INTERACTIVE=true; shift ;;
            --pulled) PULLED=true; shift ;;
            -h|--help) show_help ;;
            *) die "Неизвестный параметр: $1 (см. --help)" ;;
        esac
    done
}

TOKEN_FROM_ARGS=false
CLI_PAYMENT_MODE=""
ENV_BOT_TOKEN="${BOT_TOKEN:-}"
ORIG_ARGS=("$@")
parse_args "$@"

# ----------------------------- Запуск через curl ------------------------------

[ "$EUID" -eq 0 ] || die "Запустите от root: sudo bash $0 ${ORIG_ARGS[*]:-}"

SCRIPT_PATH="${BASH_SOURCE[0]:-}"
SCRIPT_DIR=""
if [ -n "$SCRIPT_PATH" ] && [ -f "$SCRIPT_PATH" ]; then
    SCRIPT_DIR="$(cd "$(dirname "$SCRIPT_PATH")" && pwd)"
fi

if [ -z "$SCRIPT_DIR" ] || [ ! -f "${SCRIPT_DIR}/bot.py" ]; then
    # Скрипт запущен без файлов проекта рядом (например, через curl): клонируем репозиторий
    echo -e "${BLUE}[ИНФО]${NC} Загрузка проекта в ${CLONE_DIR}..."
    command -v git >/dev/null 2>&1 || { apt-get update -q && apt-get install -y -q git; }
    if [ -d "${CLONE_DIR}/.git" ]; then
        git -C "$CLONE_DIR" pull --ff-only
    else
        git clone "$REPO_URL" "$CLONE_DIR"
    fi
    if [ ! -t 0 ] && [ -r /dev/tty ]; then
        exec bash "${CLONE_DIR}/install.sh" "${ORIG_ARGS[@]}" </dev/tty
    fi
    exec bash "${CLONE_DIR}/install.sh" "${ORIG_ARGS[@]}"
fi

if [ "$NON_INTERACTIVE" = false ] && [ ! -t 0 ] && [[ "$MODE" =~ ^(install|reconfigure|uninstall)$ ]]; then
    die "Нет терминала для вопросов мастера. Запустите в терминале или добавьте -y."
fi

if [ -z "${VPN_INSTALL_LOGGING:-}" ]; then
    export VPN_INSTALL_LOGGING=1
    mkdir -p "$(dirname "$LOG_FILE")"
    exec > >(tee -a "$LOG_FILE") 2>&1
fi
echo "==== $(date '+%F %T') install.sh ${MODE} ====" >>"$LOG_FILE"

# ----------------------------- Ввод -------------------------------------------

ask() {  # ask VAR "Вопрос" [по умолчанию]
    local __var="$1" __prompt="$2" __def="${3:-}" __in=""
    if [ "$NON_INTERACTIVE" = true ]; then
        printf -v "$__var" '%s' "$__def"
        return
    fi
    if [ -n "$__def" ]; then
        read -r -p "$__prompt [$__def]: " __in
    else
        read -r -p "$__prompt: " __in
    fi
    __in="$(trim "$__in")"
    printf -v "$__var" '%s' "${__in:-$__def}"
}

confirm() {  # confirm "Вопрос" y|n
    local def="${2:-y}" ans hint="[Y/n]"
    if [ "$NON_INTERACTIVE" = true ]; then
        [ "$def" = y ]
        return
    fi
    [ "$def" = n ] && hint="[y/N]"
    read -r -p "$1 $hint: " ans
    ans="${ans:-$def}"
    [[ "$ans" =~ ^[YyДд] ]]
}

# ----------------------------- Работа с .env ----------------------------------

# Разбор и запись .env без потери чужих ключей и комментариев.
# Значения пишутся в двойных кавычках, \ и " экранируются (формат python-dotenv).
ENVTOOL_PY='
import os, re, sys, tempfile
LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")

def decode(v):
    if len(v) >= 2 and v[0] == v[-1] == "\"":
        return re.sub(r"\\([\"\\\\$`])", r"\1", v[1:-1])
    if len(v) >= 2 and v[0] == v[-1] == "\x27":
        return v[1:-1]
    return re.sub(r"\s+#.*$", "", v)

def encode(v):
    return "\"" + v.replace("\\", "\\\\").replace("\"", "\\\"") + "\""

def parse(path):
    vals = {}
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            m = LINE.match(line.rstrip("\n"))
            if m:
                vals[m.group(1)] = decode(m.group(2))
    return vals

cmd, path = sys.argv[1], sys.argv[2]
if cmd == "dump":
    for k, v in parse(path).items():
        sys.stdout.write(k + "\0" + v + "\0")
elif cmd == "setmany":
    data = sys.stdin.buffer.read().decode("utf-8").split("\0")
    updates = dict(zip(data[0::2], data[1::2]))
    lines = open(path, encoding="utf-8").read().splitlines() if os.path.exists(path) else []
    out, seen = [], set()
    for line in lines:
        m = LINE.match(line)
        if m and m.group(1) in updates:
            k = m.group(1)
            if k not in seen:
                out.append(k + "=" + encode(updates[k]))
                seen.add(k)
        else:
            out.append(line)
    if not lines:
        out.append("# Конфигурация 2S-UI-TGbot-Dashboard (ключи обновляет install.sh, свои можно добавлять)")
    for k in updates:
        if k not in seen:
            out.append(k + "=" + encode(updates[k]))
    d = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(dir=d)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
'

declare -A ENV_EXISTING=()

env_load_file() {  # env_load_file FILE ASSOC_NAME
    local file="$1" k v
    local -n target="$2"
    [ -f "$file" ] || return 0
    while IFS= read -r -d '' k && IFS= read -r -d '' v; do
        target["$k"]="$v"
    done < <(python3 -c "$ENVTOOL_PY" dump "$file")
}

env_write() {
    local k
    mkdir -p "$INSTALL_DIR"
    if [ -f "$ENV_FILE" ]; then
        mkdir -p "$BACKUP_DIR" && chmod 700 "$BACKUP_DIR"
        cp -a "$ENV_FILE" "${BACKUP_DIR}/env-$(date +%Y%m%d-%H%M%S)"
    fi
    for k in "${ENV_KEYS[@]}"; do
        printf '%s\0%s\0' "$k" "${!k}"
    done | python3 -c "$ENVTOOL_PY" setmany "$ENV_FILE"
}

load_values() {
    local src="" k
    declare -A from_config=()
    if [ -f "$ENV_FILE" ]; then
        src="$ENV_FILE"
    elif [ -f /root/vpn-bot.env.bak ]; then
        src="/root/vpn-bot.env.bak"
    fi
    [ -n "$src" ] && env_load_file "$src" ENV_EXISTING
    if [ -n "$CONFIG_FILE" ]; then
        [ -f "$CONFIG_FILE" ] || die "Файл --config не найден: $CONFIG_FILE"
        env_load_file "$CONFIG_FILE" from_config
    fi
    for k in "${ENV_KEYS[@]}"; do
        local v="${ENV_EXISTING[$k]:-}"
        [ -n "${from_config[$k]:-}" ] && v="${from_config[$k]}"
        [ -n "${CLI[$k]:-}" ] && v="${CLI[$k]}"
        printf -v "$k" '%s' "$v"
    done
    [ -z "$BOT_TOKEN" ] && [ -n "$ENV_BOT_TOKEN" ] && BOT_TOKEN="$ENV_BOT_TOKEN"

    # SUB_DOMAIN хранится как host или host:port (бот строит ссылки https://SUB_DOMAIN/имя)
    SUB_HOST="${SUB_DOMAIN%%:*}"
    if [ -z "$HTTPS_PORT" ]; then
        if [[ "$SUB_DOMAIN" == *:* ]]; then HTTPS_PORT="${SUB_DOMAIN##*:}"; else HTTPS_PORT="443"; fi
    fi
    SUB_HOST="$(echo "$SUB_HOST" | sed -E 's~^https?://~~; s~/+$~~')"
    MAIN_DOMAIN="$(echo "$MAIN_DOMAIN" | sed -E 's~^https?://~~; s~/+$~~')"
    : "${BOT_DATA_DB:=${INSTALL_DIR}/bot_data.db}"
}

compose_panel_sub_url() {  # полный адрес подписок панели для страницы дашборда
    PANEL_SUB_URL="${PANEL_SCHEME}://${MAIN_DOMAIN}:${PANEL_PORT}${PANEL_SUB_PATH%/}"
}

compose_sub_domain() {
    if [ "$HTTPS_PORT" = "443" ]; then SUB_DOMAIN="$SUB_HOST"; else SUB_DOMAIN="${SUB_HOST}:${HTTPS_PORT}"; fi
}

card_requisites() {  # текст реквизитов по номеру карты (\n — перевод строки для бота)
    printf '%s' "💳 <b>ПЕРЕВОД ПО НОМЕРУ КАРТЫ:</b>\n<code>$1</code>\n<i>(нажмите на номер, чтобы скопировать)</i>"
}

# ----------------------------- Telegram API -----------------------------------

# Токен передаётся curl через stdin, чтобы не светиться в списке процессов
tg_api() {  # tg_api METHOD [аргументы curl...]
    local method="$1"; shift
    printf 'url = "https://api.telegram.org/bot%s/%s"\n' "$BOT_TOKEN" "$method" \
        | curl -s --max-time 15 -K - "$@" || true
}

json_get() {  # json_get ключ [ключ...] < json
    python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(0)
for p in sys.argv[1:]:
    d = d.get(p) if isinstance(d, dict) else None
print("" if d is None else d)' "$@"
}

tg_send_admin() {  # tg_send_admin "текст" -> печатает описание ошибки или пусто
    local resp
    resp="$(tg_api sendMessage --data-urlencode "chat_id=${ADMIN_TG_ID}" --data-urlencode "text=$1")"
    if [ "$(printf '%s' "$resp" | json_get ok)" = "True" ]; then
        return 0
    fi
    printf '%s' "$resp" | json_get description
    return 1
}

validate_token() {  # заполняет BOT_USERNAME, возвращает 1 если токен не принят
    [[ "$BOT_TOKEN" =~ ^[0-9]{5,15}:[A-Za-z0-9_-]{30,}$ ]] || return 1
    local resp
    resp="$(tg_api getMe)"
    BOT_USERNAME="$(printf '%s' "$resp" | json_get result username)"
    [ -n "$BOT_USERNAME" ]
}

# ----------------------------- База панели ------------------------------------

panel_sql() { sqlite3 -readonly "$DB_PATH" "$1" 2>/dev/null || true; }

find_panel_db() {
    [ -n "$DB_PATH" ] && [ -f "$DB_PATH" ] && return
    if [ -f "$DEFAULT_DB_PATH" ]; then
        DB_PATH="$DEFAULT_DB_PATH"
        return
    fi
    local found
    found="$(find /usr/local /opt /etc /root /var/lib -maxdepth 5 -name 's-ui.db' -type f 2>/dev/null | head -n1 || true)"
    [ -n "$found" ] && DB_PATH="$found"
}

validate_panel_db() {  # печатает недостающие колонки, код 1 при ошибке
    [ -f "$DB_PATH" ] || { echo "файл не найден"; return 1; }
    local cols missing="" c
    cols=" $(panel_sql "select name from pragma_table_info('clients')" | tr '\n' ' ') "
    [ "$cols" = "  " ] && { echo "нет таблицы clients (это не база 2S-UI?)"; return 1; }
    for c in $REQUIRED_COLUMNS; do
        [[ "$cols" == *" $c "* ]] || missing+=" $c"
    done
    [ -z "$missing" ] || { echo "нет колонок:${missing}"; return 1; }
}

read_panel_settings() {
    PANEL_SUB_PORT="$(panel_sql "select value from settings where key='subPort'")"
    PANEL_SUB_PATH="$(panel_sql "select value from settings where key='subPath'")"
    PANEL_SUB_DOMAIN="$(panel_sql "select value from settings where key='subDomain'")"
    PANEL_SUB_CERT="$(panel_sql "select value from settings where key='subCertFile'")"
    PANEL_SUB_PATH="${PANEL_SUB_PATH:-/sub/}"
    : "${MAIN_DOMAIN:=$PANEL_SUB_DOMAIN}"
    : "${PANEL_PORT:=${PANEL_SUB_PORT:-2096}}"
    [[ "$PANEL_SUB_PATH" == /* ]] || PANEL_SUB_PATH="/${PANEL_SUB_PATH}"
    [[ "$PANEL_SUB_PATH" == */ ]] || PANEL_SUB_PATH="${PANEL_SUB_PATH}/"
    if [ -n "$PANEL_SUB_CERT" ]; then PANEL_SCHEME="https"; else PANEL_SCHEME="http"; fi
}

# ----------------------------- Сеть -------------------------------------------

valid_host() { [[ "$1" =~ ^([A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,}$ ]]; }

port_owner() {  # имя процесса, слушающего TCP-порт, или пусто
    ss -ltnpH "( sport = :$1 )" 2>/dev/null | grep -oE 'users:\(\("[^"]+"' | head -n1 | sed 's/users:(("//; s/"$//' || true
}

dns_points_here() {
    local resolved ip
    resolved="$(getent ahostsv4 "$1" 2>/dev/null | awk '{print $1}' | sort -u || true)"
    [ -n "$resolved" ] || return 1
    for ip in $resolved; do
        [[ " $(hostname -I 2>/dev/null) " == *" $ip "* ]] && return 0
    done
    return 1
}

cert_exists() { [ -f "/etc/letsencrypt/live/${SUB_HOST}/fullchain.pem" ] && [ -f "/etc/letsencrypt/live/${SUB_HOST}/privkey.pem" ]; }

# ----------------------------- Проверки окружения -----------------------------

check_os() {
    [ -f /etc/os-release ] || die "Не удалось определить операционную систему."
    . /etc/os-release
    if [[ "$ID" != "ubuntu" && "$ID" != "debian" ]]; then
        log_warning "Скрипт рассчитан на Ubuntu и Debian, обнаружена: ${PRETTY_NAME:-$ID}."
        confirm "Продолжить?" n || exit 1
    fi
    log_success "Система: ${PRETTY_NAME:-$ID}"
}

check_python() {
    command -v python3 >/dev/null 2>&1 || return 0
    if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
        die "Нужен Python 3.10 или новее, установлен $(python3 --version 2>&1). Обновите систему (Ubuntu 22.04+ / Debian 12+)."
    fi
}

install_packages() {
    section "Системные пакеты"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y -q
    apt-get install -y -q python3 python3-venv python3-pip git curl ca-certificates \
        nginx certbot sqlite3 rsync iproute2 cron
    check_python
    log_success "Пакеты установлены."
}

# ----------------------------- Мастер -----------------------------------------

wizard() {
    section "1/8 База панели 2S-UI"
    find_panel_db
    while true; do
        ask DB_PATH "Путь к s-ui.db" "${DB_PATH:-$DEFAULT_DB_PATH}"
        local err
        if err="$(validate_panel_db)"; then
            break
        fi
        log_warning "База ${DB_PATH} не подходит: ${err}"
        [ "$NON_INTERACTIVE" = true ] && die "Укажите правильный путь через --db."
    done
    read_panel_settings
    log_success "Клиентов в панели: $(panel_sql 'select count(*) from clients')"

    section "2/8 Telegram-бот"
    if [ "$TOKEN_FROM_ARGS" = true ]; then
        log_warning "Токен передан аргументом и мог сохраниться в истории команд (history -c)."
    fi
    while true; do
        if [ "$NON_INTERACTIVE" = false ]; then
            local input_token
            if [ -n "$BOT_TOKEN" ]; then
                read -r -s -p "Токен от @BotFather [Enter — оставить сохранённый …${BOT_TOKEN: -4}]: " input_token
            else
                read -r -s -p "Токен от @BotFather (ввод скрыт): " input_token
            fi
            echo
            input_token="$(trim "$input_token")"
            [ -n "$input_token" ] && BOT_TOKEN="$input_token"
        fi
        if validate_token; then
            log_success "Бот: @${BOT_USERNAME}"
            break
        fi
        log_warning "Telegram не принял токен (или нет связи с api.telegram.org)."
        [ "$NON_INTERACTIVE" = true ] && die "Проверьте токен."
        BOT_TOKEN=""
    done

    section "3/8 Администратор"
    while true; do
        ask ADMIN_TG_ID "Ваш цифровой Telegram ID (узнать: @userinfobot)" "$ADMIN_TG_ID"
        ADMIN_TG_ID="$(echo "$ADMIN_TG_ID" | tr -cd '0-9')"
        [ -n "$ADMIN_TG_ID" ] && break
        [ "$NON_INTERACTIVE" = true ] && die "Укажите --admin."
    done
    if [ "$NON_INTERACTIVE" = false ]; then
        echo "Если ещё не писали боту, откройте https://t.me/${BOT_USERNAME} и нажмите «Старт»."
        read -r -p "Затем нажмите Enter для проверки связи..." _
    fi
    while true; do
        local tg_err
        if tg_err="$(tg_send_admin "✅ Проверка связи: установщик ${PROJECT_NAME:-VPN} может писать вам от имени бота.")"; then
            log_success "Тестовое сообщение отправлено, проверьте Telegram."
            break
        fi
        log_warning "Не удалось написать администратору: ${tg_err:-нет ответа}"
        [ "$NON_INTERACTIVE" = true ] && break
        confirm "Повторить проверку?" y || break
    done

    section "4/8 Оформление"
    ask PROJECT_NAME "Название сервиса" "${PROJECT_NAME:-My VPN Service}"
    ask SUPPORT_BOT_USERNAME "Юзернейм поддержки без @" "${SUPPORT_BOT_USERNAME:-$BOT_USERNAME}"
    SUPPORT_BOT_USERNAME="${SUPPORT_BOT_USERNAME#@}"
    ask SERVICE_GROUP_NAME "Название инфо-канала" "${SERVICE_GROUP_NAME:-Инфо-канал сервиса}"
    ask SERVICE_GROUP_URL "Ссылка на инфо-канал" "${SERVICE_GROUP_URL:-https://t.me/your_channel}"

    section "5/8 Оплата и тарифы"
    local pay_mode="card"
    if [ -n "$CLI_PAYMENT_MODE" ]; then
        pay_mode="$CLI_PAYMENT_MODE"
    elif [ -n "$PAYMENT_REQUISITES" ] && [ "$PAYMENT_REQUISITES" != "$(card_requisites "$CARD_NUMBER")" ]; then
        pay_mode="custom"
    fi
    if [ "$NON_INTERACTIVE" = false ]; then
        echo "Реквизиты: 1 — номер карты, 2 — свой текст (HTML, перевод строки — \\n)"
        local choice
        ask choice "Вариант" "$([ "$pay_mode" = custom ] && echo 2 || echo 1)"
        [ "$choice" = "2" ] && pay_mode="custom" || pay_mode="card"
    fi
    if [ "$pay_mode" = "card" ]; then
        ask CARD_NUMBER "Номер карты" "${CARD_NUMBER:-0000 0000 0000 0000}"
        PAYMENT_REQUISITES="$(card_requisites "$CARD_NUMBER")"
        [ "$CARD_NUMBER" = "0000 0000 0000 0000" ] && log_warning "Номер карты не задан — в боте будет заглушка."
    else
        ask PAYMENT_REQUISITES "Текст реквизитов" "$PAYMENT_REQUISITES"
    fi
    while true; do
        echo "Тарифы в формате месяцы:цена[:скидка%] через запятую."
        ask TARIFFS "Тарифы" "${TARIFFS:-$DEFAULT_TARIFFS}"
        TARIFFS="$(echo "$TARIFFS" | tr -d ' ')"
        [[ "$TARIFFS" =~ ^[0-9]+:[0-9]+(:[0-9]{1,2})?(,[0-9]+:[0-9]+(:[0-9]{1,2})?)*$ ]] && break
        log_warning "Неверный формат. Пример: ${DEFAULT_TARIFFS}"
        [ "$NON_INTERACTIVE" = true ] && die "Исправьте --tariffs."
    done

    section "6/8 Домены"
    ask MAIN_DOMAIN "Домен панели 2S-UI (ссылки https://домен:порт${PANEL_SUB_PATH}имя)" "${MAIN_DOMAIN:-$PANEL_SUB_DOMAIN}"
    ask PANEL_PORT "Порт подписок панели" "${PANEL_PORT:-${PANEL_SUB_PORT:-2096}}"
    compose_panel_sub_url
    while true; do
        ask SUB_HOST "Поддомен дашборда (на него бот выдаёт ссылки)" "${SUB_HOST:-${MAIN_DOMAIN:+sub.$MAIN_DOMAIN}}"
        SUB_HOST="$(echo "$SUB_HOST" | sed -E 's~^https?://~~; s~/+$~~; s~:.*$~~')"
        valid_host "$SUB_HOST" && break
        log_warning "'${SUB_HOST}' не похоже на домен (проверьте раскладку)."
        [ "$NON_INTERACTIVE" = true ] && die "Укажите --domain."
    done
    DNS_OK=true
    if dns_points_here "$SUB_HOST"; then
        log_success "DNS ${SUB_HOST} указывает на этот сервер."
    else
        DNS_OK=false
        log_warning "DNS ${SUB_HOST} не указывает на IP этого сервера ($(hostname -I | awk '{print $1}'))."
        log_warning "Добавьте A-запись, иначе сертификат не выпустится и ссылки бота не откроются."
    fi

    local owner80 owner_https
    owner80="$(port_owner 80)"
    if [ -n "$owner80" ] && [ "$owner80" != "nginx" ]; then
        die "Порт 80 занят процессом «${owner80}». Он нужен nginx для сайта и Let's Encrypt."
    fi
    while true; do
        owner_https="$(port_owner "$HTTPS_PORT")"
        [ -z "$owner_https" ] || [ "$owner_https" = "nginx" ] && break
        log_warning "Порт ${HTTPS_PORT} занят процессом «${owner_https}» (часто это сам VPN)."
        [ "$NON_INTERACTIVE" = true ] && die "Укажите свободный порт через --https-port."
        ask HTTPS_PORT "Порт HTTPS для дашборда" "8443"
    done
    compose_sub_domain

    section "7/8 Часовой пояс"
    local sys_tz
    sys_tz="$(timedatectl show -p Timezone --value 2>/dev/null || true)"
    [ "$sys_tz" = "Etc/UTC" ] || [ "$sys_tz" = "UTC" ] && sys_tz=""
    while true; do
        ask TIMEZONE "Часовой пояс для дат в уведомлениях" "${TIMEZONE:-${sys_tz:-Europe/Moscow}}"
        [ -f "/usr/share/zoneinfo/${TIMEZONE}" ] && break
        log_warning "Нет такого часового пояса: ${TIMEZONE}"
        [ "$NON_INTERACTIVE" = true ] && die "Исправьте --tz."
    done

    section "8/8 SSL"
    if cert_exists; then
        log_success "Сертификат для ${SUB_HOST} уже есть, будет использован."
        SETUP_SSL="existing"
    elif [ "$SETUP_SSL" != "n" ]; then
        if [ "$DNS_OK" = false ] && [ "$NON_INTERACTIVE" = true ]; then
            SETUP_SSL="n"
            log_warning "DNS не готов — сертификат не запрашивается."
        elif confirm "Получить бесплатный сертификат Let's Encrypt?" y; then
            SETUP_SSL="y"
            ask SSL_EMAIL "Email для уведомлений Let's Encrypt (можно пусто)" "$SSL_EMAIL"
        else
            SETUP_SSL="n"
        fi
    fi

    print_plan
    if [ "$NON_INTERACTIVE" = false ]; then
        confirm "Применить?" y || { echo "Отменено."; exit 0; }
    fi
}

print_plan() {
    section "Параметры"
    cat <<EOF
  Бот:                @${BOT_USERNAME} (админ ${ADMIN_TG_ID})
  Сервис:             ${PROJECT_NAME}
  Поддержка:          @${SUPPORT_BOT_USERNAME}
  Инфо-канал:         ${SERVICE_GROUP_NAME} (${SERVICE_GROUP_URL})
  Тарифы:             ${TARIFFS}
  База панели:        ${DB_PATH}
  Подписки панели:    https://${MAIN_DOMAIN}:${PANEL_PORT}${PANEL_SUB_PATH}<имя>
  Дашборд:            https://${SUB_DOMAIN}/<имя>
  Часовой пояс:       ${TIMEZONE}
  SSL:                ${SETUP_SSL}
  Папка установки:    ${INSTALL_DIR} (службы от пользователя ${SERVICE_USER})
EOF
}

# ----------------------------- Развёртывание ----------------------------------

source_dir() {  # откуда брать код
    if [ -d "${SCRIPT_DIR}/.git" ]; then
        echo "$SCRIPT_DIR"
    elif [ -d "${CLONE_DIR}/.git" ]; then
        echo "$CLONE_DIR"
    else
        echo "$SCRIPT_DIR"
    fi
}

ensure_user() {
    if ! id -u "$SERVICE_USER" >/dev/null 2>&1; then
        useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$SERVICE_USER"
        log_info "Создан системный пользователь ${SERVICE_USER}."
    fi
}

sync_code() {
    local src="$1"
    mkdir -p "$INSTALL_DIR"
    if [ "$(cd "$src" && pwd)" != "$(cd "$INSTALL_DIR" && pwd)" ]; then
        rsync -a --exclude 'venv' --exclude '__pycache__' --exclude '.env' --exclude '*.db' \
            --exclude '.git' --exclude 'index.html.original_backup' "${src}/" "${INSTALL_DIR}/"
    fi
    if [ ! -f "$BOT_DATA_DB" ] && [ -f /root/bot_data.db.bak ]; then
        log_info "Восстановление bot_data.db из /root/bot_data.db.bak..."
        cp -a /root/bot_data.db.bak "$BOT_DATA_DB"
    fi
}

setup_venv() {
    [ -x "${INSTALL_DIR}/venv/bin/python" ] || python3 -m venv "${INSTALL_DIR}/venv"
    "${INSTALL_DIR}/venv/bin/pip" install --upgrade pip --quiet --disable-pip-version-check
    "${INSTALL_DIR}/venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt" --quiet --disable-pip-version-check
}

install_units() {
    local db_dir name
    db_dir="$(dirname "$DB_PATH")"
    for name in vpn-bot vpn-dashboard; do
        # В шаблонах стоят пути по умолчанию, чтобы файл оставался рабочим и при простом копировании
        sed -e "s|/var/www/2S-UI-TGbot-Dashboard|${INSTALL_DIR}|g" -e "s|/usr/local/s-ui/db|${db_dir}|g" \
            -e "s|^User=.*|User=${SERVICE_USER}|" -e "s|^Group=.*|Group=${SERVICE_USER}|" \
            -e "/^WorkingDirectory=/a Environment=TZ=${TIMEZONE:-Europe/Moscow}" \
            "${INSTALL_DIR}/${name}.service" > "/etc/systemd/system/${name}.service"
    done
    systemctl daemon-reload
    systemctl enable -q vpn-bot vpn-dashboard
}

# ----------------------------- Nginx и SSL ------------------------------------

nginx_proxy_locations() {
    cat <<EOF
    add_header X-Content-Type-Options nosniff always;
    # В адресе страницы имя подписки — не передаём его другим сайтам
    add_header Referrer-Policy no-referrer always;
    limit_req_status 429;

    # Веб-дашборд: страница подписки и API статистики
    location / {
        limit_req zone=vpn_sub_rl burst=30 nodelay;
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    # Красивая ссылка https://${SUB_DOMAIN}${PANEL_SUB_PATH}<имя> -> подписка панели
    # (путь передаётся целиком, панели нужен SNI с её доменом)
    location ${PANEL_SUB_PATH} {
        limit_req zone=vpn_sub_rl burst=30 nodelay;
        proxy_pass ${PANEL_SCHEME}://127.0.0.1:${PANEL_PORT};
        proxy_ssl_server_name on;
        proxy_ssl_name ${MAIN_DOMAIN};
        proxy_ssl_verify off;
        proxy_set_header Host ${MAIN_DOMAIN};
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
EOF
}

write_nginx_conf() {  # write_nginx_conf ssl|http
    local redirect_port=""
    [ "$HTTPS_PORT" != "443" ] && redirect_port=":${HTTPS_PORT}"
    {
        echo "# Управляется install.sh (2S-UI-TGbot-Dashboard), ручные правки перезапишутся"
        echo "limit_req_zone \$binary_remote_addr zone=vpn_sub_rl:10m rate=30r/m;"
        echo
        echo "server {"
        echo "    listen 80;"
        echo "    server_name ${SUB_HOST};"
        echo
        echo "    location /.well-known/acme-challenge/ {"
        echo "        root ${CERTBOT_DIR};"
        echo "    }"
        echo
        if [ "$1" = "ssl" ]; then
            echo "    location / {"
            echo "        return 301 https://\$host${redirect_port}\$request_uri;"
            echo "    }"
        else
            nginx_proxy_locations
        fi
        echo "}"
        if [ "$1" = "ssl" ]; then
            echo
            echo "server {"
            echo "    listen ${HTTPS_PORT} ssl;"
            echo "    server_name ${SUB_HOST};"
            echo
            echo "    ssl_certificate /etc/letsencrypt/live/${SUB_HOST}/fullchain.pem;"
            echo "    ssl_certificate_key /etc/letsencrypt/live/${SUB_HOST}/privkey.pem;"
            echo "    ssl_protocols TLSv1.2 TLSv1.3;"
            echo
            nginx_proxy_locations
            echo "}"
        fi
    } > "$NGINX_CONF"
}

apply_nginx() {  # apply_nginx ssl|http — с откатом при ошибке конфигурации
    local backup=""
    if [ -f "$NGINX_CONF" ]; then
        backup="$(mktemp)"
        cp -a "$NGINX_CONF" "$backup"
    fi
    write_nginx_conf "$1"
    ln -sf "$NGINX_CONF" "$NGINX_LINK"
    if ! nginx -t >/dev/null 2>&1; then
        nginx -t || true
        if [ -n "$backup" ]; then
            cp -a "$backup" "$NGINX_CONF"
            log_warning "Прежний конфиг nginx восстановлен."
        fi
        die "Ошибка в конфигурации nginx."
    fi
    [ -n "$backup" ] && rm -f "$backup"
    systemctl enable -q nginx
    if systemctl is-active -q nginx; then systemctl reload nginx; else systemctl start nginx; fi
}

setup_nginx_ssl() {
    section "Nginx и SSL"
    mkdir -p "$CERTBOT_DIR"
    if cert_exists; then
        apply_nginx ssl
        log_success "HTTPS: https://${SUB_DOMAIN}/"
    else
        apply_nginx http
        if [ "$SETUP_SSL" = "y" ]; then
            log_info "Запрос сертификата Let's Encrypt для ${SUB_HOST}..."
            local email_args=(--register-unsafely-without-email)
            [ -n "$SSL_EMAIL" ] && email_args=(-m "$SSL_EMAIL")
            if certbot certonly --webroot -w "$CERTBOT_DIR" -d "$SUB_HOST" \
                    --non-interactive --agree-tos --keep-until-expiring "${email_args[@]}"; then
                apply_nginx ssl
                log_success "Сертификат получен, HTTPS: https://${SUB_DOMAIN}/"
            else
                log_warning "Сертификат не получен (DNS ещё не обновился?). Сайт работает по HTTP."
                log_warning "Позже: sudo bash ${INSTALL_DIR}/install.sh reconfigure"
            fi
        else
            log_warning "Сайт работает без HTTPS, а бот выдаёт ссылки https:// — они не откроются."
        fi
    fi
    mkdir -p "$(dirname "$RENEW_HOOK")"
    printf '#!/bin/sh\n# Перезагрузка nginx после продления сертификата (install.sh)\nsystemctl reload nginx\n' > "$RENEW_HOOK"
    chmod 755 "$RENEW_HOOK"
}

# ----------------------------- Дополнительно ----------------------------------

setup_extras() {
    section "Дополнительно"
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
        ufw allow 80/tcp >/dev/null
        ufw allow "${HTTPS_PORT}/tcp" >/dev/null
        log_info "ufw: открыты порты 80 и ${HTTPS_PORT}."
    fi

    cat > "$BACKUP_CRON" <<EOF
#!/bin/sh
# Ежедневный бэкап базы бота и .env (install.sh), хранится 7 дней
mkdir -p ${BACKUP_DIR} && chmod 700 ${BACKUP_DIR}
sqlite3 ${BOT_DATA_DB} ".backup '${BACKUP_DIR}/bot_data-\$(date +%Y%m%d).db'" 2>/dev/null
cp -a ${ENV_FILE} ${BACKUP_DIR}/env-daily 2>/dev/null
find ${BACKUP_DIR} -name 'bot_data-*.db' -mtime +7 -delete
EOF
    chmod 755 "$BACKUP_CRON"
    log_info "Ежедневный бэкап bot_data.db: ${BACKUP_DIR}"

    tg_api setMyCommands --data-urlencode 'commands=[{"command":"start","description":"Главное меню"}]' >/dev/null
    tg_api setMyCommands \
        --data-urlencode 'commands=[{"command":"start","description":"Главное меню"},{"command":"admin","description":"Сервисное меню"}]' \
        --data-urlencode "scope={\"type\":\"chat\",\"chat_id\":${ADMIN_TG_ID}}" >/dev/null
    log_info "Команды бота добавлены в меню Telegram."
}

# ----------------------------- Проверка работы --------------------------------

HC_FAILED=0
hc() {  # hc "описание" команда...
    local desc="$1"; shift
    if "$@" >/dev/null 2>&1; then
        echo -e "  [${GREEN}✅${NC}] ${desc}"
    else
        echo -e "  [${RED}❌${NC}] ${desc}"
        HC_FAILED=1
    fi
}

service_user_is() { [ "$(ps -o user= -p "$(systemctl show -p MainPID --value "$1")" 2>/dev/null | xargs)" = "$SERVICE_USER" ]; }
services_not_root() { service_user_is vpn-bot && service_user_is vpn-dashboard; }
bot_polling() { journalctl -u vpn-bot --since "$HC_SINCE" -o cat 2>/dev/null | grep -q "Run polling"; }
bot_db_ok() { ! journalctl -u vpn-bot --since "$HC_SINCE" -o cat 2>/dev/null | grep -qE "Ошибка выборки клиентов|Ошибка чтения БД|unable to open database|readonly database"; }
dashboard_api_ok() {
    local name
    name="$(panel_sql 'select name from clients limit 1')"
    if [ -n "$name" ]; then
        [ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:8000/api/subscription/$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1]))' "$name")")" = "200" ]
    else
        [ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8000/)" = "200" ]
    fi
}
site_ok() {
    local scheme="http" port="80"
    cert_exists && { scheme="https"; port="$HTTPS_PORT"; }
    [ "$(curl -sk -o /dev/null -w '%{http_code}' --resolve "${SUB_HOST}:${port}:127.0.0.1" "${scheme}://${SUB_HOST}:${port}/")" = "200" ]
}

health_check() {  # health_check "journalctl --since"
    HC_SINCE="$1"
    HC_FAILED=0
    section "Проверка"
    hc "vpn-bot запущен" systemctl is-active -q vpn-bot
    hc "vpn-dashboard запущен" systemctl is-active -q vpn-dashboard
    hc "службы работают от ${SERVICE_USER}, не от root" services_not_root
    hc "бот подключился к Telegram" bot_polling
    hc "бот читает базу панели" bot_db_ok
    hc "дашборд отдаёт данные из базы" dashboard_api_ok
    hc "сайт ${SUB_HOST} отвечает через nginx" site_ok
    if cert_exists; then
        local end
        end="$(openssl x509 -enddate -noout -in "/etc/letsencrypt/live/${SUB_HOST}/fullchain.pem" 2>/dev/null | cut -d= -f2)"
        echo "  [ℹ️] сертификат действует до: ${end}"
    fi
    if [ "$HC_FAILED" -ne 0 ]; then
        echo -e "\n${YELLOW}Последние строки журнала бота:${NC}"
        journalctl -u vpn-bot --since "$HC_SINCE" -n 20 --no-pager -o cat 2>/dev/null || true
    fi
    return "$HC_FAILED"
}

restart_and_check() {
    local start_ts
    start_ts="$(date +%s)"
    systemctl restart vpn-bot vpn-dashboard
    log_info "Ожидание запуска служб..."
    local i
    for i in $(seq 1 30); do
        if journalctl -u vpn-bot --since "@${start_ts}" -o cat 2>/dev/null | grep -q "Run polling"; then break; fi
        sleep 1
    done
    # Первая проверка подписок в боте стартует через 15 секунд — ждём её, чтобы увидеть ошибки чтения базы
    local elapsed=$(( $(date +%s) - start_ts ))
    [ "$elapsed" -lt 20 ] && sleep $(( 20 - elapsed ))
    health_check "@${start_ts}"
}

send_report() {
    local clients tg_ids commit
    clients="$(panel_sql 'select count(*) from clients')"
    tg_ids="$(panel_sql "select count(*) from clients where tg_id > 0 or desc glob '*[0-9][0-9][0-9][0-9][0-9]*' or remark glob '*[0-9][0-9][0-9][0-9][0-9]*'")"
    commit="$(git -C "$(source_dir)" rev-parse --short HEAD 2>/dev/null || echo '?')"
    tg_send_admin "🚀 ${PROJECT_NAME}: $1
Версия: ${commit}
Клиентов в панели: ${clients}, с Telegram ID: ~${tg_ids}
Дашборд: https://${SUB_DOMAIN}/" >/dev/null || true
}

# ----------------------------- Режимы -----------------------------------------

do_install() {
    print_banner
    check_os
    check_python
    load_values
    [ "$MODE" = "install" ] && install_packages
    wizard
    section "Установка"
    ensure_user
    sync_code "$(source_dir)"
    setup_venv
    env_write
    install_units
    setup_nginx_ssl
    setup_extras
    if restart_and_check; then
        send_report "установка завершена"
        print_summary
    else
        log_error "Установка завершена с ошибками, см. проверку выше и журнал: journalctl -u vpn-bot -n 50"
        exit 1
    fi
}

do_update() {
    [ -f "$ENV_FILE" ] || die "Не найдено ${ENV_FILE}. Сначала выполните установку: sudo bash install.sh"
    local src
    src="$(source_dir)"
    if [ "$PULLED" = false ] && [ -d "${src}/.git" ]; then
        log_info "Получение обновлений из Git (${src})..."
        git -C "$src" pull --ff-only || log_warning "git pull не удался, используются локальные файлы."
        # Продолжаем уже новой версией установщика
        exec bash "${src}/install.sh" update --pulled
    fi
    load_values
    [ -n "$TIMEZONE" ] || TIMEZONE="$(timedatectl show -p Timezone --value 2>/dev/null || echo Europe/Moscow)"
    find_panel_db
    read_panel_settings
    [ -n "$PANEL_SUB_URL" ] || compose_panel_sub_url

    section "Обновление"
    mkdir -p "$BACKUP_DIR" && chmod 700 "$BACKUP_DIR"
    local snapshot="${BACKUP_DIR}/update-$(date +%Y%m%d-%H%M%S).tar.gz"
    tar -czf "$snapshot" -C "$INSTALL_DIR" --exclude=venv --exclude=__pycache__ .
    log_info "Снимок текущей версии: ${snapshot}"

    ensure_user
    sync_code "$src"
    setup_venv
    env_write
    install_units
    apply_nginx "$(cert_exists && echo ssl || echo http)"
    if restart_and_check; then
        ls -1t "${BACKUP_DIR}"/update-*.tar.gz 2>/dev/null | tail -n +6 | xargs -r rm -f
        send_report "обновление установлено"
        log_success "Обновление завершено."
        return
    fi

    log_error "После обновления проверка не прошла — откат к предыдущей версии."
    tar -xzf "$snapshot" -C "$INSTALL_DIR" --exclude='./.env' --exclude='*.db'
    setup_venv
    install_units
    apply_nginx "$(cert_exists && echo ssl || echo http)"
    if restart_and_check; then
        log_warning "Откат выполнен, работает предыдущая версия."
    else
        log_error "Предыдущая версия тоже не запустилась. Журнал: journalctl -u vpn-bot -n 50"
    fi
    exit 1
}

do_status() {
    [ -f "$ENV_FILE" ] || die "Установка не найдена (${ENV_FILE})."
    load_values
    find_panel_db
    read_panel_settings
    local since
    since="$(systemctl show -p ActiveEnterTimestamp --value vpn-bot 2>/dev/null)"
    [ -n "$since" ] || since="-1h"
    validate_token && echo "  Бот: @${BOT_USERNAME}" || echo "  Бот: токен не принят Telegram"
    echo "  Дашборд: https://${SUB_DOMAIN}/"
    local errors
    errors="$(journalctl -u vpn-bot -u vpn-dashboard --since -24h -o cat 2>/dev/null | grep -cE '^ERROR|Traceback' || true)"
    echo "  Ошибок в журнале за сутки: ${errors}"
    health_check "$since" || exit 1
}

do_uninstall() {
    print_banner
    echo "Будут удалены службы vpn-bot и vpn-dashboard, сайт nginx ${NGINX_CONF},"
    echo "ежедневный бэкап, пользователь ${SERVICE_USER} и папка ${INSTALL_DIR}."
    echo "Сохранятся: бэкап .env и bot_data.db в ${BACKUP_DIR}, сертификаты, панель 2S-UI, ${CLONE_DIR}."
    confirm "Удалить?" "$([ "$NON_INTERACTIVE" = true ] && echo y || echo n)" || exit 0

    mkdir -p "$BACKUP_DIR" && chmod 700 "$BACKUP_DIR"
    local ts
    ts="$(date +%Y%m%d-%H%M%S)"
    [ -f "$ENV_FILE" ] && cp -a "$ENV_FILE" "${BACKUP_DIR}/env-uninstall-${ts}"
    [ -f "${INSTALL_DIR}/bot_data.db" ] && cp -a "${INSTALL_DIR}/bot_data.db" "${BACKUP_DIR}/bot_data-uninstall-${ts}.db"
    log_info "Бэкап сохранён в ${BACKUP_DIR}"

    systemctl disable --now vpn-bot vpn-dashboard 2>/dev/null || true
    rm -f /etc/systemd/system/vpn-bot.service /etc/systemd/system/vpn-dashboard.service
    systemctl daemon-reload
    rm -f "$NGINX_LINK" "$NGINX_CONF" "$BACKUP_CRON" "$RENEW_HOOK"
    if nginx -t >/dev/null 2>&1; then systemctl reload nginx 2>/dev/null || true; fi
    rm -rf "$INSTALL_DIR"
    id -u "$SERVICE_USER" >/dev/null 2>&1 && userdel "$SERVICE_USER" 2>/dev/null || true
    log_success "Удалено. Восстановить можно повторной установкой: .env и bot_data.db подхватятся из бэкапа вручную."
}

# ----------------------------- Вывод ------------------------------------------

print_banner() {
    clear 2>/dev/null || true
    echo -e "${CYAN}${BOLD}"
    echo "=================================================================="
    echo "      🚀 2S-UI-TGbot-Dashboard — установщик (${MODE})"
    echo "=================================================================="
    echo -e "${NC}"
}

print_summary() {
    echo ""
    echo -e "${GREEN}${BOLD}==================================================================${NC}"
    echo -e "${GREEN}${BOLD}  🎉 Готово: бот и дашборд работают и запускаются автоматически${NC}"
    echo -e "${GREEN}${BOLD}==================================================================${NC}"
    cat <<EOF

  Бот:                    https://t.me/${BOT_USERNAME}
  Ссылки от бота:         https://${SUB_DOMAIN}/<имя клиента>
  Подписка панели:        https://${MAIN_DOMAIN}:${PANEL_PORT}${PANEL_SUB_PATH}<имя клиента>
  Короткая ссылка:        https://${SUB_DOMAIN}${PANEL_SUB_PATH}<имя клиента>

  Telegram ID клиента впишите в панели 2S-UI в поле «Описание» или «Telegram ID».

  Проверка:               sudo bash ${INSTALL_DIR}/install.sh status
  Изменить настройки:     sudo bash ${INSTALL_DIR}/install.sh reconfigure
  Обновить:               sudo bash ${INSTALL_DIR}/install.sh update
  Логи бота:              journalctl -u vpn-bot -f
  Лог установщика:        ${LOG_FILE}
EOF
}

case "$MODE" in
    install|reconfigure) do_install ;;
    update) do_update ;;
    status) do_status ;;
    uninstall) do_uninstall ;;
esac
