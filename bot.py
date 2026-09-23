import os
import re
import time
import sqlite3
import asyncio
import logging
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# Загружаем переменные окружения из .env
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(ENV_PATH)

# --- НАСТРОЙКИ ---
API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_TG_ID = int(os.getenv("ADMIN_TG_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "/usr/local/s-ui/db/s-ui.db")
PROJECT_NAME = os.getenv("PROJECT_NAME", "VPN Service")
SUB_DOMAIN = os.getenv("SUB_DOMAIN", "sub.example.com")
SUPPORT_BOT_USERNAME = os.getenv("SUPPORT_BOT_USERNAME", "your_support_bot")
SERVICE_GROUP_NAME = os.getenv("SERVICE_GROUP_NAME", "Инфо-канал сервиса")
SERVICE_GROUP_URL = os.getenv("SERVICE_GROUP_URL", "https://t.me/your_channel")
PAYMENT_REQUISITES = os.getenv(
    "PAYMENT_REQUISITES",
    "💳 <b>ПЕРЕВОД ПО НОМЕРУ КАРТЫ:</b>\n<code>0000 0000 0000 0000</code>\n<i>(нажмите на номер, чтобы скопировать)</i>"
).replace("\\n", "\n")

# База данных для истории отправленных уведомлений (защита от дублирования)
BOT_DATA_DB = os.getenv("BOT_DATA_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_data.db"))

if not API_TOKEN or not ADMIN_TG_ID:
    raise ValueError("Ошибка: переменные BOT_TOKEN и ADMIN_TG_ID должны быть заданы в файле .env!")
# -----------------

class BuySubscriptionState(StatesGroup):
    waiting_for_receipt = State()

class BroadcastState(StatesGroup):
    waiting_for_content = State()
    waiting_for_delete_time = State()
    waiting_for_confirmation = State()

TARIFFS = {
    "1m": {
        "title": "1 месяц",
        "price": 200,
        "button": "1 месяц — 200 ₽",
        "discount": None,
    },
    "3m": {
        "title": "3 месяца",
        "price": 570,
        "button": "3 месяца — 570 ₽ (-5%)",
        "discount": "скидка 5%",
    },
    "6m": {
        "title": "6 месяцев",
        "price": 1080,
        "button": "6 месяцев — 1080 ₽ (-10%)",
        "discount": "скидка 10%",
    },
    "12m": {
        "title": "12 месяцев",
        "price": 2000,
        "button": "12 месяцев — 2000 ₽ (-15%)",
        "discount": "скидка 15%",
    },
}

logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

def get_main_menu_keyboard(user_id: int | None = None) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="💳 Купить подписку", callback_data="buy_subscription")],
        [InlineKeyboardButton(text="📦 Ваша подписка", callback_data="my_subscription")],
        [InlineKeyboardButton(text="💬 Поддержка", url=f"https://t.me/{SUPPORT_BOT_USERNAME}")],
    ]
    if user_id == ADMIN_TG_ID:
        buttons.append([InlineKeyboardButton(text="⚙️ Сервисное меню (Админ)", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_main_menu_text(full_name: str) -> str:
    return (
        f"🚀 <b>{PROJECT_NAME}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👋 Привет, <b>{full_name}</b>!\n\n"
        "Добро пожаловать в сервис быстрого и свободного интернета без цензуры и замедлений.\n\n"
        "⚡ <b>Протоколы:</b> Hysteria2, TUIC, VLESS\n"
        "📱 <b>До 2 устройств</b> на одну подписку\n"
        "🚀 <b>250 ГБ трафика в месяц</b> на максимальной скорости\n"
        "🛡 <b>Стабильный обход блокировок 24/7</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Выберите нужное действие ниже:</i>"
    )

@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer(
        get_main_menu_text(message.from_user.full_name),
        reply_markup=get_main_menu_keyboard(message.from_user.id),
        parse_mode="HTML"
    )

@dp.callback_query(F.data == "main_menu")
async def cb_main_menu(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    text = get_main_menu_text(callback.from_user.full_name)
    keyboard = get_main_menu_keyboard(callback.from_user.id)
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")

@dp.callback_query(F.data == "buy_subscription")
async def cb_buy_subscription(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    
    buttons = []
    for code, t in TARIFFS.items():
        buttons.append([InlineKeyboardButton(text=t["button"], callback_data=f"buy_tariff:{code}")])
    buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")])
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    
    text = (
        "💳 <b>Оформление подписки</b>\n\n"
        "<b>Каждый тариф включает:</b>\n"
        "📱 Одновременное подключение: <b>до 2 устройств</b>\n"
        "🚀 Трафик: <b>250 ГБ в месяц</b> без ограничений скорости\n"
        "⚡ Доступные протоколы: <b>Hysteria2, TUIC, VLESS</b>\n"
        "🛡 Обход блокировок и стабильная работа 24/7\n\n"
        "<i>Выберите подходящий период подписки:</i>"
    )
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")

@dp.callback_query(F.data.startswith("buy_tariff:"))
async def cb_buy_tariff(callback: types.CallbackQuery, state: FSMContext):
    tariff_code = callback.data.split(":")[1]
    tariff = TARIFFS.get(tariff_code)
    if not tariff:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    await state.set_state(BuySubscriptionState.waiting_for_receipt)
    await state.update_data(tariff_code=tariff_code, tariff_title=tariff["title"], tariff_price=tariff["price"])
    await callback.answer()
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Выбрать другой тариф", callback_data="buy_subscription")],
            [InlineKeyboardButton(text="❌ Отмена", callback_data="main_menu")],
        ]
    )
    
    discount_line = f" • <i>{tariff['discount']}</i>" if tariff.get("discount") else ""
    
    text = (
        f"💳 <b>ОПЛАТА ПОДПИСКИ: {tariff['title'].upper()} — {tariff['price']} ₽</b>{discount_line}\n\n"
        "<b>ПАРАМЕТРЫ ТАРИФА:</b>\n"
        "• Лимит устройств: <b>до 2 устройств</b>\n"
        "• Трафик: <b>250 ГБ / месяц</b>\n\n"
        f"{PAYMENT_REQUISITES}\n\n"
        "⚠️ <b>ИНСТРУКЦИЯ ПО ОПЛАТЕ:</b>\n"
        f"<b>1. ПЕРЕВЕДИТЕ {tariff['price']} ₽ ПО НОМЕРУ КАРТЫ ВЫШЕ</b>\n"
        "<b>2. ПРИШЛИТЕ СКРИНШОТ КВИТАНЦИИ / ЧЕКА ОБ ОПЛАТЕ В ЭТОТ ЧАТ (ФОТО ИЛИ ФАЙЛОМ)</b>\n"
        "<b>3. ПОСЛЕ ОТПРАВКИ ЧЕКА АДМИНИСТРАТОР ПРОВЕРИТ ПЛАТЁЖ И АКТИВИРУЕТ ДОСТУП</b>"
    )
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")

@dp.message(BuySubscriptionState.waiting_for_receipt, F.photo | F.document)
async def handle_receipt_media(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    username = message.from_user.username
    user_mention = f"@{username}" if username else f"ID: {user_id}"
    full_name = message.from_user.full_name
    
    data = await state.get_data()
    tariff_title = data.get("tariff_title", "1 месяц")
    tariff_price = data.get("tariff_price", 200)
    tariff_code = data.get("tariff_code", "1m")
    
    await state.clear()
    
    admin_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Одобрить", callback_data=f"approve_sub:{user_id}:{tariff_code}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject_sub:{user_id}"),
            ]
        ]
    )
    
    caption = (
        f"💳 <b>Новая заявка на оплату!</b>\n\n"
        f" Тариф: <b>{tariff_title} — {tariff_price} ₽</b>\n"
        f"📱 Устройств: <b>2</b> | 🚀 Трафик: <b>250 ГБ/мес</b>\n"
        f"👤 Пользователь: {full_name} ({user_mention})\n"
        f"🆔 Telegram ID: <code>{user_id}</code>\n\n"
        "Проверьте поступление средств и выберите действие:"
    )
    
    # Пересылаем чек админу
    try:
        if message.photo:
            file_id = message.photo[-1].file_id
            await bot.send_photo(ADMIN_TG_ID, photo=file_id, caption=caption, reply_markup=admin_keyboard, parse_mode="HTML")
        elif message.document:
            file_id = message.document.file_id
            await bot.send_document(ADMIN_TG_ID, document=file_id, caption=caption, reply_markup=admin_keyboard, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Не удалось переслать чек админу: {e}")
    
    user_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="◀️ В главное меню", callback_data="main_menu")]
        ]
    )
    await message.answer(
        "⏳ <b>Чек принят на проверку!</b>\n\n"
        f"Вы выбрали тариф: <b>{tariff_title} — {tariff_price} ₽</b>.\n"
        "Мы передали ваш чек администратору. Обычно проверка занимает от 5 до 15 минут.\n"
        "Как только оплата будет подтверждена, вам придёт уведомление с кнопкой для подключения.",
        reply_markup=user_keyboard,
        parse_mode="HTML"
    )

@dp.message(BuySubscriptionState.waiting_for_receipt)
async def handle_receipt_invalid(message: types.Message):
    cancel_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="main_menu")]
        ]
    )
    await message.answer(
        "⚠️ <b>ПОЖАЛУЙСТА, ОТПРАВЬТЕ СКРИНШОТ ЧЕКА ОБ ОПЛАТЕ (КАК ФОТО ИЛИ ФАЙЛ).</b>\n\n"
        "Если вы хотите отменить оплату, нажмите кнопку ниже:",
        reply_markup=cancel_keyboard,
        parse_mode="HTML"
    )

@dp.callback_query(F.data.startswith("approve_sub:"))
async def cb_admin_approve_sub(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_TG_ID:
        await callback.answer("⛔ У вас нет прав для этого действия!", show_alert=True)
        return
    
    parts = callback.data.split(":")
    target_user_id = int(parts[1])
    tariff_code = parts[2] if len(parts) > 2 else "1m"
    tariff_title = TARIFFS.get(tariff_code, {}).get("title", "выбранный период")
    
    await callback.answer("Подписка одобрена ✅")
    
    try:
        if callback.message.caption:
            new_caption = callback.message.caption + "\n\n<b>Статус:</b> ✅ ОДОБРЕНО"
            await callback.message.edit_caption(caption=new_caption, reply_markup=None, parse_mode="HTML")
        else:
            new_text = callback.message.text + "\n\n<b>Статус:</b> ✅ ОДОБРЕНО"
            await callback.message.edit_text(text=new_text, reply_markup=None, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Ошибка редактирования сообщения админа: {e}")

    user_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📦 Ваша подписка", callback_data="my_subscription")],
            [InlineKeyboardButton(text="◀️ Главное меню", callback_data="main_menu")],
        ]
    )
    try:
        await bot.send_message(
            target_user_id,
            f"🎉 <b>Ваша оплата подтверждена!</b>\n\n"
            f"Подписка на <b>{tariff_title}</b> успешно активирована.\n"
            "Нажмите кнопку <b>«📦 Ваша подписка»</b> ниже для получения ссылки на подключение:",
            reply_markup=user_keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление пользователю {target_user_id}: {e}")

@dp.callback_query(F.data.startswith("reject_sub:"))
async def cb_admin_reject_sub(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_TG_ID:
        await callback.answer("⛔ У вас нет прав для этого действия!", show_alert=True)
        return
    
    target_user_id = int(callback.data.split(":")[1])
    await callback.answer("Подписка отклонена ❌")
    
    try:
        if callback.message.caption:
            new_caption = callback.message.caption + "\n\n<b>Статус:</b> ❌ ОТКЛОНЕНО"
            await callback.message.edit_caption(caption=new_caption, reply_markup=None, parse_mode="HTML")
        else:
            new_text = callback.message.text + "\n\n<b>Статус:</b> ❌ ОТКЛОНЕНО"
            await callback.message.edit_text(text=new_text, reply_markup=None, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Ошибка редактирования сообщения админа: {e}")

    try:
        await bot.send_message(
            target_user_id,
            "❌ <b>Оплата не подтверждена.</b>\n\n"
            "К сожалению, администратор не подтвердил получение перевода по вашему чеку.\n"
            "Если у вас есть вопросы или произошла ошибка, свяжитесь с поддержкой.",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление пользователю {target_user_id}: {e}")

@dp.callback_query(F.data == "my_subscription")
async def cb_my_subscription(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    username = callback.from_user.username
    user_mention = f"@{username}" if username else f"ID: {user_id}"
    full_name = callback.from_user.full_name
    
    await callback.answer()
    
    # Ищем подписки пользователя в базе
    subs = find_subscriptions_by_tg_id(str(user_id))
    
    if subs:
        # Успешный кейс: подписка найдена
        links_text = "\n".join([f"🔗 {s['name']} ({s['date_str']}): https://{SUB_DOMAIN}/{s['name']}" for s in subs])
        
        # Отправляем красивый отчет вам (админу)
        try:
            await bot.send_message(
                ADMIN_TG_ID,
                f"✅ <b>Успешный запрос подписки:</b>\n"
                f"👤 Пользователь: {full_name} ({user_mention})\n"
                f"🆔 Telegram ID: <code>{user_id}</code>\n"
                f"📦 Найдено подписок: {len(subs)}\n\n"
                f"{links_text}",
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"Не удалось отправить уведомление админу: {e}")

        # Формируем inline-кнопки со ссылками на подписки: Имя профиля | Дата окончания
        sub_buttons = []
        for s in subs:
            btn_title = f"{s['name']} | {s['date_str']}"
            sub_buttons.append([InlineKeyboardButton(text=btn_title, url=f"https://{SUB_DOMAIN}/{s['name']}")])
        
        sub_buttons.append([InlineKeyboardButton(text=f"👥 {SERVICE_GROUP_NAME}", url=SERVICE_GROUP_URL)])
        sub_buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")])
        sub_keyboard = InlineKeyboardMarkup(inline_keyboard=sub_buttons)

        text = (
            f"👋 Найдено активных подписок: <b>{len(subs)}</b>\n\n"
            "Нажмите на кнопку с вашей подпиской для подключения.\n\n"
            "📢 <b>Информация и новости сервиса:</b>\n"
            f"Вся актуальная информация по работе сервиса публикуется в нашей группе "
            f"<a href=\"{SERVICE_GROUP_URL}\">{SERVICE_GROUP_NAME}</a>."
        )
        try:
            await callback.message.edit_text(text, reply_markup=sub_keyboard, parse_mode="HTML")
        except Exception:
            await callback.message.answer(text, reply_markup=sub_keyboard, parse_mode="HTML")
    else:
        # Неуспешный кейс: ID нет в базе
        # Уведомляем вас, что кто-то посторонний или новый стучится в бот
        try:
            await bot.send_message(
                ADMIN_TG_ID,
                f"⚠️ <b>Попытка доступа без подписки:</b>\n"
                f"👤 Пользователь: {full_name} ({user_mention})\n"
                f"🆔 Telegram ID: <code>{user_id}</code>\n"
                f"<i>Его ID не найден в базе панели.</i>",
                parse_mode="HTML"
            )
        except Exception as e:
            logging.error(f"Не удалось отправить уведомление админу: {e}")

        back_keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="◀️ Назад", callback_data="main_menu")]
            ]
        )
        text = "❌ У вас пока нет активных подписок или ваш Telegram ID не привязан администратором."
        try:
            await callback.message.edit_text(text, reply_markup=back_keyboard, parse_mode="HTML")
        except Exception:
            await callback.message.answer(text, reply_markup=back_keyboard, parse_mode="HTML")

def init_bot_data_db():
    """Создает локальные таблицы истории отправленных уведомлений и очереди автоудалений."""
    try:
        conn = sqlite3.connect(BOT_DATA_DB)
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS notification_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_name TEXT NOT NULL,
                telegram_id INTEGER NOT NULL,
                notification_type TEXT NOT NULL,
                expiry_ts INTEGER NOT NULL,
                sent_at INTEGER NOT NULL,
                UNIQUE(client_name, notification_type, expiry_ts)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_deletions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                delete_at INTEGER NOT NULL,
                created_at INTEGER NOT NULL
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_delete_at ON scheduled_deletions(delete_at)")
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Ошибка инициализации базы bot_data.db: {e}")

def schedule_message_deletion(chat_id: int, message_id: int, delete_at: int):
    """Добавляет сообщение в очередь на автоудаление."""
    try:
        conn = sqlite3.connect(BOT_DATA_DB)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO scheduled_deletions (chat_id, message_id, delete_at, created_at)
            VALUES (?, ?, ?, ?)
        """, (chat_id, message_id, delete_at, int(time.time())))
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Ошибка сохранения сообщения на автоудаление: {e}")

def get_due_deletions(now_ts: int) -> list:
    """Возвращает сообщения, чей срок жизни истек."""
    try:
        conn = sqlite3.connect(BOT_DATA_DB)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, chat_id, message_id FROM scheduled_deletions
            WHERE delete_at <= ?
        """, (now_ts,))
        rows = cursor.fetchall()
        conn.close()
        return rows
    except Exception as e:
        logging.error(f"Ошибка выборки scheduled_deletions: {e}")
        return []

def delete_scheduled_record(record_id: int):
    """Удаляет запись об автоудалении из базы после выполнения."""
    try:
        conn = sqlite3.connect(BOT_DATA_DB)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM scheduled_deletions WHERE id = ?", (record_id,))
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Ошибка удаления записи scheduled_deletions: {e}")

def is_notification_sent(client_name: str, notification_type: str, expiry_ts: int) -> bool:
    """Проверяет, отправлялось ли уже уведомление этого типа для данного периода подписки."""
    try:
        conn = sqlite3.connect(BOT_DATA_DB)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT 1 FROM notification_history
            WHERE client_name = ? AND notification_type = ? AND expiry_ts = ?
            LIMIT 1
        """, (client_name, notification_type, expiry_ts))
        row = cursor.fetchone()
        conn.close()
        return row is not None
    except Exception as e:
        logging.error(f"Ошибка проверки notification_history: {e}")
        return False

def record_notification_sent(client_name: str, telegram_id: int, notification_type: str, expiry_ts: int):
    """Записывает факт отправки уведомления в историю."""
    try:
        conn = sqlite3.connect(BOT_DATA_DB)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR IGNORE INTO notification_history 
            (client_name, telegram_id, notification_type, expiry_ts, sent_at)
            VALUES (?, ?, ?, ?, ?)
        """, (client_name, telegram_id, notification_type, expiry_ts, int(time.time())))
        conn.commit()
        conn.close()
    except Exception as e:
        logging.error(f"Ошибка записи в notification_history: {e}")

def extract_tg_id(desc: str | None, remark: str | None, tg_id: int | None) -> int | None:
    """Извлекает цифровой Telegram ID из полей клиента панели."""
    if tg_id and tg_id > 0:
        return int(tg_id)
    for val in (desc, remark):
        if not val:
            continue
        val_str = str(val).strip()
        if val_str.isdigit() and len(val_str) >= 5:
            return int(val_str)
        match = re.search(r'\b\d{6,15}\b', val_str)
        if match:
            return int(match.group(0))
    return None

async def check_and_send_expiry_notifications():
    """Проверяет клиентов в s-ui.db и отправляет уведомления за 7 дней, 3 дня, 12 часов и при истечении."""
    if not os.path.exists(DB_PATH):
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT name, desc, remark, tg_id, expiry, enable
            FROM clients
            WHERE enable = 1 AND expiry > 0
        """)
        clients = cursor.fetchall()
        conn.close()
    except Exception as e:
        logging.error(f"Ошибка выборки клиентов из s-ui.db: {e}")
        return

    now_ts = int(time.time())

    for row in clients:
        name = row[0]
        desc = row[1]
        remark = row[2]
        tg_id_raw = row[3]
        expiry_raw = int(row[4] or 0)

        if expiry_raw <= 0:
            continue

        tg_id = extract_tg_id(desc, remark, tg_id_raw)
        if not tg_id:
            continue

        expiry_ts = int(expiry_raw / 1000 if expiry_raw > 100_000_000_000 else expiry_raw)
        time_left = expiry_ts - now_ts

        # Классификация по интервалам
        notif_type = None
        if -86400 < time_left <= 0:
            notif_type = "expired"
        elif 0 < time_left <= 12 * 3600:
            notif_type = "12h"
        elif 12 * 3600 < time_left <= 3 * 86400:
            notif_type = "3d"
        elif 3 * 86400 < time_left <= 7 * 86400:
            notif_type = "7d"

        if not notif_type:
            continue

        # Проверяем, не отправляли ли уже такое уведомление для текущего периода
        if is_notification_sent(name, notif_type, expiry_ts):
            continue

        expiry_dt = datetime.fromtimestamp(expiry_ts)
        date_str = expiry_dt.strftime("%d.%m.%Y")
        time_str = expiry_dt.strftime("%H:%M")

        # Формируем текст и клавиатуру
        if notif_type == "7d":
            text = (
                f"⏳ <b>Напоминание: подписка заканчивается через 7 дней</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"👋 Здравствуйте! Напоминаем, что действие вашей VPN-подписки <b>{name}</b> истекает:\n"
                f"📅 <b>{date_str}</b>\n\n"
                f"Вы можете продлить доступ заранее — при оплате новые дни прибавятся к текущему сроку!\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"<i>Нажмите кнопку ниже для быстрого продления:</i>"
            )
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="💳 Продлить подписку", callback_data="buy_subscription")],
                    [InlineKeyboardButton(text="📦 Ваша подписка", callback_data="my_subscription")],
                ]
            )
        elif notif_type == "3d":
            text = (
                f"⚠️ <b>Внимание: до окончания подписки осталось 3 дня!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Срок действия вашей VPN-подписки <b>{name}</b> заканчивается:\n"
                f"📅 <b>{date_str}</b>\n\n"
                f"Чтобы интернет оставался быстрым и защищенным без перерывов в работе, рекомендуем продлить доступ прямо сейчас.\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"<i>Продление займет всего минуту:</i>"
            )
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="💳 Продлить сейчас", callback_data="buy_subscription")],
                    [InlineKeyboardButton(text="📦 Ваша подписка", callback_data="my_subscription")],
                    [InlineKeyboardButton(text="💬 Поддержка", url=f"https://t.me/{SUPPORT_BOT_USERNAME}")],
                ]
            )
        elif notif_type == "12h":
            text = (
                f"🚨 <b>Срочно: подписка истекает менее чем через 12 часов!</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Ваш VPN-профиль <b>{name}</b> будет приостановлен:\n"
                f"📅 <b>{date_str} в {time_str}</b>\n\n"
                f"Продлите тариф прямо сейчас, чтобы ваши устройства не отключились от сервиса.\n"
                f"━━━━━━━━━━━━━━━━━━━━"
            )
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="💳 Срочно продлить", callback_data="buy_subscription")],
                    [InlineKeyboardButton(text="💬 Поддержка", url=f"https://t.me/{SUPPORT_BOT_USERNAME}")],
                ]
            )
        else: # expired
            text = (
                f"❌ <b>Срок действия вашей подписки завершен</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
                f"Действие профиля <b>{name}</b> окончено <b>{date_str}</b>.\n"
                f"Сервер приостановил подключение.\n\n"
                f"Вы можете возобновить доступ в любой момент — просто выберите нужный тариф ниже:"
            )
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [InlineKeyboardButton(text="💳 Возобновить подписку", callback_data="buy_subscription")],
                    [InlineKeyboardButton(text="💬 Поддержка", url=f"https://t.me/{SUPPORT_BOT_USERNAME}")],
                ]
            )

        # Отправляем сообщение
        try:
            await bot.send_message(tg_id, text, reply_markup=keyboard, parse_mode="HTML")
            logging.info(f"Успешно отправлено уведомление [{notif_type}] клиенту {name} (TG ID: {tg_id})")
        except Exception as e:
            logging.warning(f"Не удалось отправить уведомление [{notif_type}] пользователю {tg_id} ({name}): {e}")

        # Фиксируем отправку в БД истории
        record_notification_sent(name, tg_id, notif_type, expiry_ts)

        # Небольшая пауза между отправками (антиспам Telegram)
        await asyncio.sleep(0.3)

async def expiry_checker_loop():
    """Фоновый цикл проверки подписок каждые 20 минут."""
    logging.info("Фоновый цикл проверки истекающих подписок запущен.")
    # Задержка 15 секунд перед первым прогоном после запуска бота
    await asyncio.sleep(15)
    while True:
        try:
            await check_and_send_expiry_notifications()
        except Exception as e:
            logging.error(f"Непредвиденная ошибка в expiry_checker_loop: {e}")
        # Проверка каждые 20 минут (1200 секунд)
        await asyncio.sleep(1200)

def find_subscriptions_by_tg_id(telegram_id: str):
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        query = """
            SELECT name, expiry FROM clients 
            WHERE desc LIKE ? OR remark LIKE ? OR tg_id = ?
        """
        tg_num = int(telegram_id) if str(telegram_id).isdigit() else 0
        cursor.execute(query, (f"%{telegram_id}%", f"%{telegram_id}%", tg_num))
        rows = cursor.fetchall()
        conn.close()
        
        subs = []
        for name, expiry in rows:
            if expiry and expiry > 0:
                ts = expiry / 1000 if expiry > 100_000_000_000 else expiry
                date_str = datetime.fromtimestamp(ts).strftime("%d.%m.%Y")
            else:
                date_str = "Бессрочно"
            subs.append({
                "name": name,
                "expiry": expiry,
                "date_str": date_str,
            })
        return subs
    except Exception as e:
        print(f"Ошибка чтения БД: {e}")
        return []

def get_authorized_telegram_ids() -> list[int]:
    """Возвращает список уникальных цифровых Telegram ID всех активных клиентов."""
    if not os.path.exists(DB_PATH):
        return []
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT desc, remark, tg_id FROM clients
            WHERE enable = 1
        """)
        rows = cursor.fetchall()
        conn.close()

        unique_ids = set()
        for desc, remark, tg_id_raw in rows:
            tg_id = extract_tg_id(desc, remark, tg_id_raw)
            if tg_id and tg_id > 0:
                unique_ids.add(tg_id)
        return sorted(list(unique_ids))
    except Exception as e:
        logging.error(f"Ошибка получения списка Telegram ID: {e}")
        return []

def get_service_stats() -> dict:
    """Возвращает сводную статистику сервиса из базы панели 2S-UI."""
    if not os.path.exists(DB_PATH):
        return {"total": 0, "active": 0, "finite": 0, "permanent": 0, "unique_tg": 0}
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        cursor.execute("SELECT count(*) FROM clients")
        total = cursor.fetchone()[0]
        
        cursor.execute("SELECT count(*) FROM clients WHERE enable = 1")
        active = cursor.fetchone()[0]
        
        cursor.execute("SELECT count(*) FROM clients WHERE enable = 1 AND expiry > 0")
        finite = cursor.fetchone()[0]
        
        cursor.execute("SELECT count(*) FROM clients WHERE enable = 1 AND (expiry <= 0 OR expiry IS NULL)")
        permanent = cursor.fetchone()[0]
        
        conn.close()
        
        unique_tg = len(get_authorized_telegram_ids())
        return {
            "total": total,
            "active": active,
            "finite": finite,
            "permanent": permanent,
            "unique_tg": unique_tg
        }
    except Exception as e:
        logging.error(f"Ошибка получения статистики сервиса: {e}")
        return {"total": 0, "active": 0, "finite": 0, "permanent": 0, "unique_tg": 0}

def parse_delete_time(text: str) -> tuple[int, str] | None:
    """
    Парсит введенное время автоудаления.
    Возвращает кортеж (unix_timestamp, описание) или None в случае некорректного ввода.
    """
    text = text.strip().lower()
    now_ts = int(time.time())

    # 1. Без удаления
    if text in ("0", "нет", "без удаления", "никогда", "бессрочно", "none"):
        return (0, "Без удаления (бессрочно)")

    # 2. Относительное время (+Xh, +Xm, +Xd)
    rel_match = re.match(r'^\+?(\d+)\s*(m|мин|h|ч|d|д)$', text)
    if rel_match:
        val = int(rel_match.group(1))
        unit = rel_match.group(2)
        if unit in ('m', 'мин'):
            seconds = val * 60
            desc = f"Через {val} мин."
        elif unit in ('h', 'ч'):
            seconds = val * 3600
            desc = f"Через {val} ч."
        else:
            seconds = val * 86400
            desc = f"Через {val} дн."
        target_ts = now_ts + seconds
        target_dt = datetime.fromtimestamp(target_ts)
        return (target_ts, f"{desc} ({target_dt.strftime('%d.%m.%Y %H:%M')})")

    # 3. Число часов без суффикса (например, '2' -> 2 часа)
    if text.isdigit():
        val = int(text)
        if 1 <= val <= 720:
            seconds = val * 3600
            target_ts = now_ts + seconds
            target_dt = datetime.fromtimestamp(target_ts)
            return (target_ts, f"Через {val} ч. ({target_dt.strftime('%d.%m.%Y %H:%M')})")

    # 4. Точная дата и время: DD.MM.YYYY HH:MM
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%y %H:%M", "%d.%m %H:%M"):
        try:
            parsed_dt = datetime.strptime(text, fmt)
            if fmt == "%d.%m %H:%M":
                parsed_dt = parsed_dt.replace(year=datetime.now().year)
            target_ts = int(parsed_dt.timestamp())
            if target_ts <= now_ts:
                return None
            return (target_ts, parsed_dt.strftime("%d.%m.%Y в %H:%M"))
        except ValueError:
            continue

    return None

async def message_cleaner_loop():
    """Фоновый воркер автоудаления сообщений рассылки каждые 30 секунд."""
    logging.info("Фоновый воркер автоудаления сообщений запущен.")
    while True:
        try:
            now_ts = int(time.time())
            due_records = get_due_deletions(now_ts)
            for rec_id, chat_id, msg_id in due_records:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=msg_id)
                except Exception as e:
                    logging.debug(f"Не удалось удалить сообщение {msg_id} у {chat_id}: {e}")
                finally:
                    delete_scheduled_record(rec_id)
        except Exception as e:
            logging.error(f"Непредвиденная ошибка в message_cleaner_loop: {e}")
        await asyncio.sleep(30)

# ----------------- СЕРВИСНОЕ МЕНЮ АДМИНИСТРАТОРА -----------------

def get_admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📢 Создать рассылку", callback_data="admin_start_broadcast")],
            [InlineKeyboardButton(text="🔄 Обновить статистику", callback_data="admin_panel")],
            [InlineKeyboardButton(text="◀️ В клиентское меню", callback_data="main_menu")],
        ]
    )

def get_admin_panel_text() -> str:
    stats = get_service_stats()
    return (
        "⚙️ <b>Сервисное меню администратора</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📊 <b>Состояние сервиса:</b>\n"
        f"• Всего профилей в 2S-UI: <b>{stats['total']}</b>\n"
        f"• Активных подписок: <b>{stats['active']}</b>\n"
        f"• Срочных (с датой окончания): <b>{stats['finite']}</b>\n"
        f"• Бессрочных тарифов: <b>{stats['permanent']}</b>\n"
        f"• Доступных клиентов в Telegram: <b>{stats['unique_tg']}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Выберите нужное действие ниже:</i>"
    )

@dp.message(Command("admin"))
async def cmd_admin(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_TG_ID:
        await message.answer("⛔ Доступ запрещен.")
        return
    await state.clear()
    await message.answer(get_admin_panel_text(), reply_markup=get_admin_panel_keyboard(), parse_mode="HTML")

@dp.callback_query(F.data == "admin_panel")
async def cb_admin_panel(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_TG_ID:
        await callback.answer("⛔ Доступ запрещен!", show_alert=True)
        return
    await state.clear()
    await callback.answer()
    text = get_admin_panel_text()
    keyboard = get_admin_panel_keyboard()
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")

# ----------------- РАССЫЛКА С АВТОУДАЛЕНИЕМ -----------------

@dp.callback_query(F.data == "admin_start_broadcast")
async def cb_admin_start_broadcast(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_TG_ID:
        await callback.answer("⛔ Доступ запрещен!", show_alert=True)
        return
    await callback.answer()
    await state.set_state(BroadcastState.waiting_for_content)
    
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel_broadcast")]
        ]
    )
    text = (
        "📢 <b>Создание новой рассылки</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Отправьте сообщение, которое хотите разослать всем авторизованным пользователям сервиса.\n\n"
        "<b>Поддерживаются:</b>\n"
        "• Обычный текст (с HTML-разметкой, ссылками, эмодзи)\n"
        "• Фотография с текстом в подписи\n\n"
        "<i>Отправьте сообщение в ответ на этот диалог или нажмите «Отмена».</i>"
    )
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")

@dp.callback_query(F.data == "admin_cancel_broadcast")
async def cb_admin_cancel_broadcast(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_TG_ID:
        return
    await state.clear()
    await callback.answer("Рассылка отменена")
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ В админ-панель", callback_data="admin_panel")]
        ]
    )
    try:
        await callback.message.edit_text("❌ <b>Создание рассылки отменено.</b>", reply_markup=keyboard, parse_mode="HTML")
    except Exception:
        await callback.message.answer("❌ <b>Создание рассылки отменено.</b>", reply_markup=keyboard, parse_mode="HTML")

@dp.message(BroadcastState.waiting_for_content, F.text | F.photo)
async def handle_broadcast_content(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_TG_ID:
        return

    if message.photo:
        photo_id = message.photo[-1].file_id
        text_content = message.html_text or message.caption or ""
        content_type = "photo"
    else:
        photo_id = None
        text_content = message.html_text or message.text or ""
        content_type = "text"

    await state.update_data(
        content_type=content_type,
        photo_id=photo_id,
        text_content=text_content
    )
    await state.set_state(BroadcastState.waiting_for_delete_time)

    ttl_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="1 час", callback_data="bttl:3600"),
                InlineKeyboardButton(text="3 часа", callback_data="bttl:10800"),
                InlineKeyboardButton(text="6 часов", callback_data="bttl:21600"),
            ],
            [
                InlineKeyboardButton(text="12 часов", callback_data="bttl:43200"),
                InlineKeyboardButton(text="24 часа", callback_data="bttl:86400"),
                InlineKeyboardButton(text="3 дня", callback_data="bttl:259200"),
            ],
            [
                InlineKeyboardButton(text="7 дней", callback_data="bttl:604800"),
                InlineKeyboardButton(text="♾️ Без удаления", callback_data="bttl:0"),
            ],
            [
                InlineKeyboardButton(text="❌ Отмена", callback_data="admin_cancel_broadcast")
            ]
        ]
    )

    await message.answer(
        "⏱ <b>Срок жизни сообщения у пользователей</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Через какое время удалить разосланное сообщение из чатов пользователей, чтобы не засорять переписку?\n\n"
        "<i>Нажмите одну из готовых кнопок или отправьте время текстом:\n"
        "• <code>+2h</code> (через 2 часа)\n"
        "• <code>+30m</code> (через 30 минут)\n"
        "• <code>20.09.2026 15:00</code> (точная дата и время)</i>",
        reply_markup=ttl_keyboard,
        parse_mode="HTML"
    )

@dp.callback_query(BroadcastState.waiting_for_delete_time, F.data.startswith("bttl:"))
async def cb_broadcast_ttl(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_TG_ID:
        return
    await callback.answer()

    seconds = int(callback.data.split(":")[1])
    now_ts = int(time.time())

    if seconds == 0:
        delete_at = 0
        delete_desc = "Без удаления (бессрочно)"
    else:
        delete_at = now_ts + seconds
        target_dt = datetime.fromtimestamp(delete_at)
        if seconds < 86400:
            hours = seconds // 3600
            delete_desc = f"Через {hours} ч. ({target_dt.strftime('%d.%m.%Y %H:%M')})"
        else:
            days = seconds // 86400
            delete_desc = f"Через {days} дн. ({target_dt.strftime('%d.%m.%Y %H:%M')})"

    await show_broadcast_preview(callback.message, state, delete_at, delete_desc)

@dp.message(BroadcastState.waiting_for_delete_time, F.text)
async def handle_broadcast_custom_ttl(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_TG_ID:
        return

    parsed = parse_delete_time(message.text)
    if not parsed:
        await message.answer(
            "⚠️ <b>Не удалось распознать время.</b>\n\n"
            "Пожалуйста, выберите кнопку или введите время в одном из форматов:\n"
            "• <code>+2h</code> (часы)\n"
            "• <code>+45m</code> (минуты)\n"
            "• <code>+3d</code> (дни)\n"
            "• <code>20.09.2026 18:00</code> (дата и время)\n"
            "• <code>0</code> (без удаления)",
            parse_mode="HTML"
        )
        return

    delete_at, delete_desc = parsed
    await show_broadcast_preview(message, state, delete_at, delete_desc)

async def show_broadcast_preview(message_or_bot_msg, state: FSMContext, delete_at: int, delete_desc: str):
    """Показывает администратору предпросмотр рассылки и запрашивает подтверждение."""
    await state.update_data(delete_at=delete_at, delete_desc=delete_desc)
    await state.set_state(BroadcastState.waiting_for_confirmation)

    data = await state.get_data()
    content_type = data.get("content_type")
    text_content = data.get("text_content")
    photo_id = data.get("photo_id")

    recipients = get_authorized_telegram_ids()
    total = len(recipients)

    await message_or_bot_msg.answer("👁 <b>ПРЕДПРОСМОТР СООБЩЕНИЯ ДЛЯ КЛИЕНТОВ:</b>", parse_mode="HTML")
    await asyncio.sleep(0.3)

    if content_type == "photo":
        await message_or_bot_msg.answer_photo(photo=photo_id, caption=text_content, parse_mode="HTML")
    else:
        await message_or_bot_msg.answer(text_content, parse_mode="HTML")

    await asyncio.sleep(0.4)

    confirm_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=f"🚀 Отправить всем ({total} чел.)", callback_data="admin_confirm_broadcast")],
            [InlineKeyboardButton(text="❌ Отменить рассылку", callback_data="admin_cancel_broadcast")],
        ]
    )

    await message_or_bot_msg.answer(
        "📋 <b>Параметры отправки:</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Получателей: <b>{total} пользователей</b>\n"
        f"🗑 Автоудаление: <b>{delete_desc}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Внимательно проверьте текст выше и подтвердите запуск:</i>",
        reply_markup=confirm_keyboard,
        parse_mode="HTML"
    )

@dp.callback_query(BroadcastState.waiting_for_confirmation, F.data == "admin_confirm_broadcast")
async def cb_admin_confirm_broadcast(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_TG_ID:
        await callback.answer("⛔ Доступ запрещен!", show_alert=True)
        return

    data = await state.get_data()
    await state.clear()
    await callback.answer()

    content_type = data.get("content_type")
    text_content = data.get("text_content")
    photo_id = data.get("photo_id")
    delete_at = data.get("delete_at", 0)
    delete_desc = data.get("delete_desc", "Без удаления")

    recipients = get_authorized_telegram_ids()
    total = len(recipients)

    if total == 0:
        await callback.message.answer("❌ Нет доступных клиентов с привязанным Telegram ID.")
        return

    status_msg = await callback.message.answer(
        f"⏳ <b>Рассылка запущена...</b>\nОбработано: 0 из {total}",
        parse_mode="HTML"
    )

    success_count = 0
    fail_count = 0

    for idx, user_id in enumerate(recipients, 1):
        try:
            if content_type == "photo":
                sent = await bot.send_photo(user_id, photo=photo_id, caption=text_content, parse_mode="HTML")
            else:
                sent = await bot.send_message(user_id, text_content, parse_mode="HTML")

            # Если включено автоудаление — планируем
            if delete_at > 0:
                schedule_message_deletion(chat_id=user_id, message_id=sent.message_id, delete_at=delete_at)

            success_count += 1
        except Exception as e:
            fail_count += 1
            logging.warning(f"Не удалось доставить сообщение рассылки клиенту {user_id}: {e}")

        # Обновляем статус каждые 5 пользователей или в самом конце
        if idx % 5 == 0 or idx == total:
            try:
                await status_msg.edit_text(
                    f"⏳ <b>Рассылка в процессе...</b>\n"
                    f"Обработано: <b>{idx}</b> из {total}\n"
                    f"Успешно: <b>{success_count}</b> | Ошибок: <b>{fail_count}</b>",
                    parse_mode="HTML"
                )
            except Exception:
                pass

        await asyncio.sleep(0.06)

    final_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⚙️ В админ-панель", callback_data="admin_panel")],
            [InlineKeyboardButton(text="◀️ В главное меню", callback_data="main_menu")],
        ]
    )

    await status_msg.edit_text(
        "✅ <b>Рассылка успешно завершена!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        f"👥 Всего адресатов: <b>{total}</b>\n"
        f"📤 Успешно доставлено: <b>{success_count}</b>\n"
        f"⚠️ Ошибок (заблокировали бота): <b>{fail_count}</b>\n"
        f"🗑 Автоудаление: <b>{delete_desc}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Если было задано автоудаление, сообщения автоматически исчезнут из чатов пользователей в назначенное время.</i>",
        reply_markup=final_keyboard,
        parse_mode="HTML"
    )

async def main():
    # Инициализация таблицы истории уведомлений и очереди автоудалений
    init_bot_data_db()
    
    # Запуск фонового планировщика проверки подписок
    asyncio.create_task(expiry_checker_loop())
    
    # Запуск фонового воркера автоудаления сообщений рассылки
    asyncio.create_task(message_cleaner_loop())
    
    print("Бот запущен и ожидает сообщения...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

