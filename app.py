from fastapi import FastAPI, Response, status
from fastapi.responses import HTMLResponse, JSONResponse
import sqlite3
import os
import time
from datetime import datetime, date

app = FastAPI(title="VPN Dashboard API")

# Пути к БД и HTML файлу с поддержкой переменных окружения и локального запуска
DB_PATH = os.getenv("DB_PATH", "/usr/local/s-ui/db/s-ui.db")

LOCAL_HTML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
DEFAULT_HTML_PATH = "/var/www/vpn-service/index.html"

def get_html_content() -> str:
    """Читает актуальный index.html с диска (локального или серверного)."""
    target_path = LOCAL_HTML_PATH if os.path.exists(LOCAL_HTML_PATH) else DEFAULT_HTML_PATH
    with open(target_path, "r", encoding="utf-8") as f:
        return f.read()

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
    bot_name = os.getenv("SUPPORT_BOT_USERNAME", os.getenv("BOT_USERNAME", "podnyatie_vpn_bot"))
    bot_link = f"https://t.me/{bot_name}" if bot_name else "#"
    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Подписка не найдена</title>
    <style>
        body {{ font-family: 'Inter', sans-serif; background: #0f172a; color: #f8fafc; display: flex; justify-content: center; align-items: center; height: 100vh; margin: 0; }}
        .error-box {{ background: #1e293b; border: 1px solid #334155; padding: 32px; border-radius: 16px; text-align: center; max-width: 400px; box-shadow: 0 10px 25px rgba(0,0,0,0.5); }}
        h2 {{ color: #ef4444; margin-top: 0; font-size: 20px; }}
        p {{ color: #94a3b8; font-size: 14px; line-height: 1.5; }}
        a {{ color: #6366f1; text-decoration: none; font-weight: 500; }}
    </style>
</head>
<body>
    <div class="error-box">
        <h2>Ошибка 404</h2>
        <p>Такой подписки не существует. Проверьте правильность ссылки.</p>
        <p><a href="{bot_link}">Перейти в Telegram-бот</a></p>
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
