import os
import re
import time
import sqlite3
from datetime import datetime, date
from fastapi import FastAPI, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
from dotenv import load_dotenv

# Загрузка переменных окружения из .env
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(ENV_PATH)

app = FastAPI(title="VPN Dashboard API")

# Пути к БД и HTML файлу с поддержкой переменных окружения и локального запуска
DB_PATH = os.getenv("DB_PATH", "/usr/local/s-ui/db/s-ui.db")

LOCAL_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
DEFAULT_HTML_PATH = "/var/www/2S-UI-TGbot-Dashboard/index.html"

def get_html_content() -> str:
    """Читает актуальный index.html и динамически подставляет конфигурацию проекта из .env."""
    target_path = LOCAL_HTML_PATH if os.path.exists(LOCAL_HTML_PATH) else DEFAULT_HTML_PATH
    with open(target_path, "r", encoding="utf-8") as f:
        content = f.read()

    project_name = os.getenv("PROJECT_NAME", "").strip() or "VPN Service"
    main_domain = os.getenv("MAIN_DOMAIN", "").strip()
    panel_port = os.getenv("PANEL_PORT", "2096").strip() or "2096"
    support_bot = os.getenv("SUPPORT_BOT_USERNAME", "").strip().lstrip("@") or "your_support_bot"
    main_bot = os.getenv("BOT_USERNAME", "").strip().lstrip("@") or support_bot

    # Замена конфигурации JS через регулярные выражения
    content = re.sub(r'window\.PROJECT_NAME\s*=\s*[^;]+;', f'window.PROJECT_NAME = "{project_name}";', content)
    content = re.sub(r'window\.PANEL_DOMAIN\s*=\s*[^;]+;', f'window.PANEL_DOMAIN = "{main_domain}";', content)
    content = re.sub(r'window\.PANEL_PORT\s*=\s*[^;]+;', f'window.PANEL_PORT = "{panel_port}";', content)
    content = re.sub(r'window\.BOT_USERNAME\s*=\s*[^;]+;', f'window.BOT_USERNAME = "{main_bot}";', content)
    content = re.sub(r'window\.SUPPORT_BOT_USERNAME\s*=\s*[^;]+;', f'window.SUPPORT_BOT_USERNAME = "{support_bot}";', content)

    # Прямая замена плейсхолдеров
    content = content.replace("{{ PROJECT_NAME }}", project_name)
    content = content.replace("{{ BOT_USERNAME }}", main_bot)
    content = content.replace("{{ SUPPORT_BOT_USERNAME }}", support_bot)

    # Обратная совместимость с устаревшей дефолтной разметкой
    content = content.replace('<h1 id="brand-title">podnyatie.space</h1>', f'<h1 id="brand-title">{project_name}</h1>')
    content = content.replace('<title>Настройка VPN - podnyatie.space</title>', f'<title>Настройка VPN - {project_name}</title>')
    content = content.replace('@podnyatie_vpn_bot', f'@{main_bot}')
    content = content.replace('https://t.me/podnyatie_vpn_bot', f'https://t.me/{main_bot}')
    content = content.replace('@podnyatie_support_bot', f'@{support_bot}')
    content = content.replace('https://t.me/podnyatie_support_bot', f'https://t.me/{support_bot}')

    return content

def format_bytes(b: int) -> str:
    """Форматирует байты в читаемый вид (Б, КБ, МБ, ГБ)."""
    if b <= 0:
        return "0 ГБ"
    gb = b / (1024 ** 3)
    if gb >= 1.0:
        return f"{gb:.1f} ГБ"
    mb = b / (1024 ** 2)
    if mb >= 1.0:
        return f"{mb:.1f} МБ"
    kb = b / 1024
    if kb >= 1.0:
        return f"{kb:.1f} КБ"
    return f"{b} Б"

def get_client_stats(username: str) -> dict | None:
    """Извлекает из базы данных s-ui.db актуальные данные по клиенту."""
    if not os.path.exists(DB_PATH):
        print(f"Предупреждение: БД не найдена по пути {DB_PATH}")
        return None

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT name, enable, volume, expiry, down, up, total_down, total_up, next_reset
            FROM clients
            WHERE name = ?
        """, (username,))
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        name = row[0]
        enable = bool(row[1])
        volume = int(row[2] or 0)
        expiry = int(row[3] or 0)
        down = int(row[4] or 0)
        up = int(row[5] or 0)
        next_reset = int(row[8] or 0)

        used = down + up
        now_ts = int(time.time())

        # Расчет срока действия подписки
        if expiry > 0:
            expiry_dt = datetime.fromtimestamp(expiry)
            expiry_formatted = expiry_dt.strftime("%d.%m.%Y")
            days_left = (expiry_dt.date() - date.today()).days
            is_expired = now_ts > expiry
            is_unlimited_expiry = False
        else:
            expiry_formatted = "Бессрочно"
            days_left = None
            is_expired = False
            is_unlimited_expiry = True

        # Расчет расхода трафика
        if volume > 0:
            is_unlimited_traffic = False
            remaining = max(0, volume - used)
            used_percent = min(100.0, round((used / volume) * 100, 1))
            volume_formatted = format_bytes(volume)
            remaining_formatted = format_bytes(remaining)
        else:
            is_unlimited_traffic = True
            remaining = None
            used_percent = 0.0
            volume_formatted = "Безлимит"
            remaining_formatted = "Безлимит"

        next_reset_formatted = datetime.fromtimestamp(next_reset).strftime("%d.%m.%Y") if next_reset > 0 else None

        return {
            "username": name,
            "enable": enable,
            "is_expired": is_expired,
            "expiry": expiry,
            "expiry_formatted": expiry_formatted,
            "is_unlimited_expiry": is_unlimited_expiry,
            "days_left": days_left,
            "volume_bytes": volume,
            "volume_formatted": volume_formatted,
            "is_unlimited_traffic": is_unlimited_traffic,
            "down_bytes": down,
            "up_bytes": up,
            "used_bytes": used,
            "used_formatted": format_bytes(used),
            "remaining_bytes": remaining,
            "remaining_formatted": remaining_formatted,
            "used_percent": used_percent,
            "next_reset": next_reset,
            "next_reset_formatted": next_reset_formatted,
            "updated_at": now_ts
        }
    except Exception as e:
        print(f"Ошибка чтения БД: {e}")
        return None

def check_user_exists(username: str) -> bool:
    """Проверяет существование пользователя в базе данных."""
    if not os.path.exists(DB_PATH):
        return False
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT 1 FROM clients WHERE name = ? LIMIT 1", (username,))
        row = cursor.fetchone()
        conn.close()
        return row is not None
    except Exception as e:
        print(f"Ошибка проверки пользователя: {e}")
        return False

# ----------------- API ЭНДПОИНТЫ -----------------

@app.api_route("/api/subscription/{username}", methods=["GET", "HEAD"])
async def subscription_api(username: str, response: Response):
    """API для получения статистики подписки (трафик, дата, статус). Кэшируется на 1 час."""
    stats = get_client_stats(username)
    if not stats:
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"error": "Подписка не найдена", "username": username}
        )

    # Кэширование информации на 1 час (3600 сек)
    response.headers["Cache-Control"] = "public, max-age=3600"
    return stats

# ----------------- ОБСЛУЖИВАНИЕ ФРОНТЕНДА -----------------

def get_404_html() -> str:
    bot_name = os.getenv("SUPPORT_BOT_USERNAME", os.getenv("BOT_USERNAME", "your_support_bot")).strip().lstrip("@")
    has_valid_bot = bot_name and bot_name != "your_support_bot"
    bot_link = f"https://t.me/{bot_name}" if has_valid_bot else "#"
    bot_label = f" (@{bot_name})" if has_valid_bot else ""
    project_name = os.getenv("PROJECT_NAME", "VPN Service").strip() or "VPN Service"
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>{project_name} - Подписка не найдена</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {{
            --bg-base: #0b0e14;
            --bg-panel: #161925;
            --bg-card: #1c1f2e;
            --border: #2a2e3f;
            --text-main: #f8fafc;
            --text-muted: #8b92a5;
            --accent-cyan: #06b6d4;
            --danger: #ef4444;
        }}
        * {{ box-sizing: border-box; }}
        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background-color: var(--bg-base);
            color: var(--text-main);
            margin: 0;
            padding: 20px 16px;
            display: flex;
            justify-content: center;
            align-items: center;
            min-height: 100vh;
            line-height: 1.4;
            -webkit-font-smoothing: antialiased;
        }}
        .error-card {{
            background: var(--bg-panel);
            border: 1px solid var(--border);
            border-radius: 16px;
            padding: 32px 24px;
            max-width: 420px;
            width: 100%;
            text-align: center;
            box-shadow: 0 12px 36px rgba(0,0,0,0.5);
        }}
        .brand {{
            font-size: 19px;
            font-weight: 700;
            color: var(--text-main);
            margin-bottom: 3px;
            letter-spacing: -0.4px;
        }}
        .brand-sub {{
            font-size: 11px;
            color: var(--text-muted);
            margin-bottom: 20px;
        }}
        .status-badge {{
            display: inline-flex;
            align-items: center;
            gap: 6px;
            background: rgba(239, 68, 68, 0.12);
            border: 1px solid rgba(239, 68, 68, 0.25);
            color: var(--danger);
            padding: 3px 10px;
            border-radius: 20px;
            font-size: 11px;
            font-weight: 600;
            margin-bottom: 14px;
        }}
        .status-dot {{
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background-color: currentColor;
            box-shadow: 0 0 6px currentColor;
        }}
        .error-title {{
            font-size: 18px;
            font-weight: 700;
            color: var(--text-main);
            margin: 0 0 8px 0;
        }}
        .error-desc {{
            color: var(--text-muted);
            font-size: 13px;
            line-height: 1.5;
            margin: 0 0 22px 0;
        }}
        .btn-bot {{
            display: inline-flex;
            align-items: center;
            justify-content: center;
            gap: 8px;
            background: var(--accent-cyan);
            color: #0b0e14;
            text-decoration: none;
            padding: 12px 20px;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 600;
            transition: all 0.2s;
            box-shadow: 0 2px 10px rgba(6, 182, 212, 0.3);
            width: 100%;
        }}
        .btn-bot:hover {{
            background: #0891b2;
            color: white;
            transform: translateY(-1px);
        }}
        .btn-bot svg {{
            width: 18px;
            height: 18px;
            fill: currentColor;
        }}
    </style>
</head>
<body>
    <div class="error-card">
        <div class="brand">{project_name}</div>
        <div class="brand-sub">Панель управления подпиской</div>
        <div class="status-badge">
            <div class="status-dot"></div>
            Ошибка 404
        </div>
        <h2 class="error-title">Подписка не найдена</h2>
        <p class="error-desc">Пользователь с такой ссылкой не найден в системе. Проверьте правильность адреса или перейдите в Telegram-бот для управления подпиской.</p>
        <a href="{bot_link}" class="btn-bot">
            <svg viewBox="0 0 24 24"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.64 6.8c-.15 1.58-.8 5.42-1.13 7.19-.14.75-.42 1-.68 1.03-.58.05-1.02-.38-1.58-.75-.88-.58-1.38-.94-2.23-1.5-.99-.65-.35-1.01.22-1.59.15-.15 2.71-2.48 2.76-2.69a.2.2 0 00-.05-.18c-.06-.05-.14-.03-.21-.02-.09.02-1.49.95-4.22 2.79-.4.27-.76.41-1.08.4-.36-.01-1.04-.2-1.55-.37-.63-.2-1.12-.31-1.08-.66.02-.18.27-.36.75-.55 2.92-1.27 4.86-2.11 5.83-2.52 2.78-1.16 3.35-1.36 3.73-1.36.08 0 .27.02.39.12.1.08.13.19.14.27-.01.06.01.24 0 .38z"/></svg>
            Перейти в Telegram-бот{bot_label}
        </a>
    </div>
</body>
</html>"""

@app.api_route("/{sub_path:path}", methods=["GET", "HEAD"])
async def verify_and_serve(sub_path: str, response: Response):
    username = sub_path.strip("/")
    
    # Корень сайта или системные пути
    if not username or username == "index.html" or username.startswith("sub/"):
        return HTMLResponse(content=get_html_content())

    # Проверяем наличие пользователя в БД
    if not check_user_exists(username):
        response.status_code = status.HTTP_404_NOT_FOUND
        return HTMLResponse(content=get_404_html(), status_code=404)

    # Если подписка найдена — отдаем интерфейс лендинга
    return HTMLResponse(content=get_html_content())
