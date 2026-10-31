import os
import re
import html
import math
import time
import sqlite3
from urllib.parse import quote
import asyncio
import logging
from datetime import datetime
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from panel_api import PanelAPI, PanelAPIError, expiry_seconds

# Загружаем переменные окружения из .env
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(ENV_PATH)

# --- НАСТРОЙКИ ---
API_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_TG_ID = int(os.getenv("ADMIN_TG_ID", "0"))
DB_PATH = os.getenv("DB_PATH", "/usr/local/s-ui/db/s-ui.db")

def connect_panel_db() -> sqlite3.Connection:
    """Открывает базу панели 2S-UI только на чтение."""
    return sqlite3.connect(f"file:{quote(DB_PATH)}?mode=ro", uri=True)

_raw_sub_domain = os.getenv("SUB_DOMAIN", "sub.example.com").strip().rstrip('/')
if _raw_sub_domain.startswith("https://"):
    SUB_DOMAIN = _raw_sub_domain[8:]
elif _raw_sub_domain.startswith("http://"):
    SUB_DOMAIN = _raw_sub_domain[7:]
else:
    SUB_DOMAIN = _raw_sub_domain
SUPPORT_BOT_USERNAME = os.getenv("SUPPORT_BOT_USERNAME", "your_support_bot")
SERVICE_GROUP_NAME = os.getenv("SERVICE_GROUP_NAME", "Инфо-канал сервиса")
SERVICE_GROUP_URL = os.getenv("SERVICE_GROUP_URL", "https://t.me/your_channel")
CARD_NUMBER = os.getenv("CARD_NUMBER", "").strip()

def card_requisites(card: str) -> str:
    return f"Карта: <code>{card}</code>\n<i>Нажмите на номер, чтобы скопировать</i>"

# Так реквизиты по номеру карты раньше записывал install.sh — показываем их в новом виде
_LEGACY_CARD_REQUISITES = re.compile(
    r"💳 <b>ПЕРЕВОД ПО НОМЕРУ КАРТЫ:</b>\n<code>([^<]+)</code>\n<i>\(нажмите на номер, чтобы скопировать\)</i>")
_raw_payment = os.getenv("PAYMENT_REQUISITES", "").strip().replace("\\n", "\n")
_legacy_card = _LEGACY_CARD_REQUISITES.fullmatch(_raw_payment)
if _legacy_card:
    PAYMENT_REQUISITES = card_requisites(_legacy_card.group(1))
elif _raw_payment:
    PAYMENT_REQUISITES = _raw_payment
else:
    PAYMENT_REQUISITES = card_requisites(CARD_NUMBER or "0000 0000 0000 0000")
PROJECT_NAME = os.getenv("PROJECT_NAME", "My VPN Service")

# База данных для истории отправленных уведомлений (защита от дублирования)
BOT_DATA_DB = os.getenv("BOT_DATA_DB", os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_data.db"))

if not API_TOKEN or not ADMIN_TG_ID:
    raise ValueError("Ошибка: переменные BOT_TOKEN и ADMIN_TG_ID должны быть заданы в файле .env!")

def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, "").strip() or default)
    except ValueError:
        print(f"Ошибка в {key}, используется {default}")
        return default

# Лимиты каждой подписки: трафик в месяц и одновременные устройства
SUB_TRAFFIC_GB = _env_int("SUB_TRAFFIC_GB", 250)
SUB_DEVICE_LIMIT = _env_int("SUB_DEVICE_LIMIT", 2)
# Число месяца, когда сбрасывается трафик (1-31); 0 — каждые 30 дней от создания подписки
SUB_RESET_DAY_OF_MONTH = min(max(_env_int("SUB_RESET_DAY_OF_MONTH", 0), 0), 31)
# Группа в панели для купленных подписок (новым всегда, при продлении — если группы нет)
SUB_GROUP = os.getenv("SUB_GROUP", "").strip()

# REST API панели: без него бот работает по-старому, подписку админ выдаёт вручную
PANEL_API_URL = os.getenv("PANEL_API_URL", "").strip()
PANEL_API_TOKEN = os.getenv("PANEL_API_TOKEN", "").strip()
PANEL_INBOUND_IDS = [int(x) for x in re.findall(r"\d+", os.getenv("PANEL_INBOUND_IDS", ""))]
if PANEL_API_URL and PANEL_API_TOKEN and PANEL_INBOUND_IDS:
    panel_api = PanelAPI(PANEL_API_URL, PANEL_API_TOKEN, PANEL_INBOUND_IDS, SUB_TRAFFIC_GB, SUB_DEVICE_LIMIT,
                         SUB_RESET_DAY_OF_MONTH, SUB_GROUP)
else:
    panel_api = None
    print("PANEL_API_URL / PANEL_API_TOKEN / PANEL_INBOUND_IDS не заданы: подписки выдаются вручную")
# -----------------

class BuySubscriptionState(StatesGroup):
    choosing_target = State()
    waiting_for_receipt = State()

class RenameState(StatesGroup):
    waiting_for_label = State()

class BroadcastState(StatesGroup):
    waiting_for_content = State()
    waiting_for_delete_time = State()
    waiting_for_confirmation = State()

DEFAULT_TARIFFS = "30:200,90:570:5,180:1080:10,365:2000:15"

# Старый формат TARIFFS был в месяцах: 1:200,3:570,...
_LEGACY_MONTH_DAYS = {1: 30, 3: 90, 6: 180, 12: 365}

def _days_title(days: int) -> str:
    if days % 10 == 1 and days % 100 != 11:
        word = "день"
    elif days % 10 in (2, 3, 4) and days % 100 not in (12, 13, 14):
        word = "дня"
    else:
        word = "дней"
    return f"{days} {word}"

def parse_tariffs(raw: str) -> dict:
    """Разбирает строку тарифов вида 'дни:цена[:скидка%],...'."""
    items = [[p.strip() for p in item.split(":")] for item in raw.split(",") if item.strip()]
    # Все сроки не больше 24 — значит, это старая запись в месяцах
    legacy_months = bool(items) and all(int(parts[0]) <= 24 for parts in items)
    tariffs = {}
    for parts in items:
        days, price = int(parts[0]), int(parts[1])
        if legacy_months:
            days = _LEGACY_MONTH_DAYS.get(days, days * 30)
        if days <= 0:
            raise ValueError(f"срок тарифа должен быть больше нуля: {parts[0]}")
        discount = int(parts[2]) if len(parts) > 2 and parts[2] else 0
        title = _days_title(days)
        tariffs[f"{days}d"] = {
            "days": days,
            "title": title,
            "price": price,
            "button": f"{title} — {price} ₽" + (f" (-{discount}%)" if discount else ""),
            "discount": f"скидка {discount}%" if discount else None,
        }
    if not tariffs:
        raise ValueError("пустой список тарифов")
    return tariffs

try:
    TARIFFS = parse_tariffs(os.getenv("TARIFFS", "").strip() or DEFAULT_TARIFFS)
except (ValueError, IndexError) as e:
    print(f"Ошибка в TARIFFS ({e}), используются тарифы по умолчанию")
    TARIFFS = parse_tariffs(DEFAULT_TARIFFS)

logging.basicConfig(level=logging.INFO)

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

def get_main_menu_keyboard(user_id: int | None = None) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text="💳 Купить подписку", callback_data="buy_subscription")],
        [InlineKeyboardButton(text="📦 Ваша подписка", callback_data="my_subscription")],
        [InlineKeyboardButton(text="Поддержка", url=f"https://t.me/{SUPPORT_BOT_USERNAME}")],
    ]
    if user_id == ADMIN_TG_ID:
        buttons.append([InlineKeyboardButton(text="⚙️ Сервисное меню (Админ)", callback_data="admin_panel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def devices_text(n: int | None = None) -> str:
    """«до 2 устройств» по SUB_DEVICE_LIMIT или лимиту конкретной подписки."""
    n = SUB_DEVICE_LIMIT if n is None else n
    if n <= 0:
        return "без ограничения устройств"
    word = "устройства" if n % 10 == 1 and n % 100 != 11 else "устройств"
    return f"до {n} {word}"

def cap(text: str) -> str:
    """Первая буква заглавная, остальные как есть (str.capitalize превратил бы «ГБ» в «гб»)."""
    return text[:1].upper() + text[1:]

def traffic_text() -> str:
    """«250 ГБ в месяц» по SUB_TRAFFIC_GB."""
    return f"{SUB_TRAFFIC_GB} ГБ в месяц" if SUB_TRAFFIC_GB > 0 else "безлимитный трафик"

def get_main_menu_text(full_name: str) -> str:
    return (
        f"🚀 <b>{PROJECT_NAME}</b>\n\n"
        f"Привет, <b>{html.escape(full_name)}</b>!\n"
        "Быстрый и свободный интернет без цензуры и замедлений.\n\n"
        "• Протоколы: Hysteria2, TUIC, VLESS\n"
        f"• {cap(devices_text())} на одну подписку\n"
        f"• {cap(traffic_text())} на максимальной скорости\n"
        "• Стабильный обход блокировок 24/7\n\n"
        "<i>Выберите действие ниже:</i>"
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

async def show_screen(callback: types.CallbackQuery, text: str, keyboard: InlineKeyboardMarkup):
    """Меняет текущее сообщение, а если не вышло — присылает новое."""
    try:
        await callback.message.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
    except Exception:
        await callback.message.answer(text, reply_markup=keyboard, parse_mode="HTML")

def tariffs_screen(header: str, back_callback: str) -> tuple[str, InlineKeyboardMarkup]:
    """Витрина тарифов: цена за месяц у длинных сроков и пометка самого выгодного."""
    items = list(TARIFFS.items())
    # Самый выгодный — с минимальной ценой за день, если это не самый короткий срок
    best = min(items, key=lambda kv: kv[1]["price"] / kv[1]["days"])[0] if len(items) > 1 else None
    if best == min(items, key=lambda kv: kv[1]["days"])[0]:
        best = None

    lines = []
    buttons = []
    for code, t in items:
        # Месяцев — округлённо (90 дней = 3, 365 = 12): так цена за месяц совпадает с ожиданием покупателя
        per_month = f" <i>({round(t['price'] / round(t['days'] / 30))} ₽/мес)</i>" if t["days"] > 31 else ""
        if code == best:
            lines.append(f"🔥 {t['title']} — <b>{t['price']} ₽</b>{per_month} — <b>выгоднее всего</b>")
            buttons.append([InlineKeyboardButton(text=t["button"], callback_data=f"buy_tariff:{code}")])
        else:
            lines.append(f"• {t['title']} — <b>{t['price']} ₽</b>{per_month}")
            buttons.append([InlineKeyboardButton(text=t["button"], callback_data=f"buy_tariff:{code}")])
    buttons.append([InlineKeyboardButton(text="← Назад", callback_data=back_callback)])

    text = (
        f"{header}\n\n"
        f"Каждый тариф: {devices_text()}, {traffic_text()}.\n"
        "Hysteria2, TUIC, VLESS, обход блокировок 24/7.\n\n"
        + "\n".join(lines)
    )
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)

@dp.callback_query(F.data == "buy_subscription")
async def cb_buy_subscription(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    text, keyboard = tariffs_screen("💳 <b>Выберите срок подписки</b>", "main_menu")
    await show_screen(callback, text, keyboard)

@dp.callback_query(F.data.startswith("renew:"))
async def cb_renew(callback: types.CallbackQuery, state: FSMContext):
    """Продление конкретной подписки: из карточки и из напоминаний, без вопроса «продлить или новую»."""
    sub = find_own_subscription(callback.from_user.id, callback.data.split(":")[1])
    if not sub:
        await callback.answer("Подписка не найдена", show_alert=True)
        return
    await state.clear()
    await state.update_data(renew_id=sub["id"], renew_name=sub["name"], renew_title=sub_title(sub))
    await callback.answer()
    text, keyboard = tariffs_screen(
        f"💳 <b>Продление «{html.escape(sub_title(sub))}»</b>\n"
        "Дни добавятся к текущему сроку, ссылка для подключения не изменится.",
        f"sub:{sub['id']}",
    )
    await show_screen(callback, text, keyboard)

@dp.callback_query(F.data.startswith("buy_tariff:"))
async def cb_buy_tariff(callback: types.CallbackQuery, state: FSMContext):
    tariff_code = callback.data.split(":")[1]
    tariff = TARIFFS.get(tariff_code)
    if not tariff:
        await callback.answer("Тариф не найден", show_alert=True)
        return

    data = await state.get_data()
    await state.update_data(
        tariff_code=tariff_code, tariff_title=tariff["title"], tariff_price=tariff["price"],
        tariff_days=tariff["days"], target_client_id=data.get("renew_id"),
        target_client_name=data.get("renew_name"), target_title=data.get("renew_title"),
    )
    await callback.answer()

    # Продление выбранной подписки — сразу к оплате
    if data.get("renew_id"):
        await show_payment(callback, state)
        return

    # Без API подписку всё равно выдаёт админ, спрашивать «продлить или новую» незачем
    subs = find_subscriptions_by_tg_id(str(callback.from_user.id)) if panel_api else []
    if not subs:
        await show_payment(callback, state)
        return

    await state.set_state(BuySubscriptionState.choosing_target)
    await state.update_data(own_subs={str(s["id"]): [s["name"], sub_title(s)] for s in subs})
    buttons = [
        [InlineKeyboardButton(text=f"Продлить «{sub_title(s)}» ({sub_short_status(s)})",
                              callback_data=f"buy_target:{s['id']}")]
        for s in subs
    ]
    buttons.append([InlineKeyboardButton(text="Новая подписка", callback_data="buy_target:new")])
    buttons.append([InlineKeyboardButton(text="← Выбрать другой срок", callback_data="buy_subscription")])
    text = (
        f"💳 <b>{tariff['title']} — {tariff['price']} ₽</b>\n\n"
        "У вас уже есть подписка. Что оплачиваем?\n\n"
        "<b>Продлить</b> — дни добавятся к текущему сроку, ссылка останется прежней.\n"
        "<b>Новая подписка</b> — отдельная ссылка, например для других устройств."
    )
    await show_screen(callback, text, InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(BuySubscriptionState.choosing_target, F.data.startswith("buy_target:"))
async def cb_buy_target(callback: types.CallbackQuery, state: FSMContext):
    target = callback.data.split(":")[1]
    data = await state.get_data()
    if target != "new":
        own = data.get("own_subs", {}).get(target)
        if not own:
            await callback.answer("Подписка не найдена, выберите заново", show_alert=True)
            return
        await state.update_data(target_client_id=int(target), target_client_name=own[0], target_title=own[1])
    await callback.answer()
    await show_payment(callback, state)

@dp.callback_query(F.data.startswith("buy_target:"))
async def cb_buy_target_stale(callback: types.CallbackQuery):
    await callback.answer("Выбор устарел, начните покупку заново", show_alert=True)

async def show_payment(callback: types.CallbackQuery, state: FSMContext):
    """Реквизиты и просьба прислать чек по выбранному тарифу."""
    data = await state.get_data()
    tariff = TARIFFS[data["tariff_code"]]
    await state.set_state(BuySubscriptionState.waiting_for_receipt)

    back = f"renew:{data['renew_id']}" if data.get("renew_id") else "buy_subscription"
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="← Выбрать другой срок", callback_data=back)],
            [InlineKeyboardButton(text="Отмена", callback_data="main_menu")],
        ]
    )

    discount = f" · <i>{tariff['discount']}</i>" if tariff.get("discount") else ""
    if data.get("target_client_id"):
        target_line = f"Продление «{html.escape(data.get('target_title') or data['target_client_name'])}»\n"
    elif panel_api:
        target_line = "Новая подписка\n"
    else:
        target_line = ""

    text = (
        f"💳 <b>Оплата: {tariff['title']} — {tariff['price']} ₽</b>{discount}\n"
        f"{target_line}\n"
        f"<b>1.</b> Переведите <b>{tariff['price']} ₽</b>:\n"
        f"{PAYMENT_REQUISITES}\n\n"
        "<b>2.</b> Пришлите сюда скриншот чека — фото или файлом.\n\n"
        "<b>3.</b> Мы проверим оплату (обычно 5–15 минут), и бот сразу пришлёт ссылку для подключения.\n\n"
        f"<i>{cap(devices_text())}, {traffic_text()}.</i>"
    )
    await show_screen(callback, text, keyboard)

def order_caption(order: dict, status_line: str = "") -> str:
    """Подпись к чеку у админа, всегда собирается заново из заявки."""
    username = order.get("username")
    user_mention = f"@{username}" if username else f"ID: {order['tg_id']}"
    if order["kind"] == "extend":
        kind = f"🔄 Продление <code>{html.escape(order['target_client_name'] or '')}</code>"
    else:
        kind = "➕ Новая подписка"
    text = (
        f"💳 <b>Заявка №{order['id']}</b>\n\n"
        f"🗓 Тариф: <b>{_days_title(order['days'])} — {order['price']} ₽</b>\n"
        f"{kind}\n"
        f"📱 Устройств: <b>{SUB_DEVICE_LIMIT or '∞'}</b> | 🚀 Трафик: <b>{traffic_text()}</b>\n"
        f"👤 Пользователь: {html.escape(order.get('full_name') or '')} ({html.escape(user_mention)})\n"
        f"🆔 Telegram ID: <code>{order['tg_id']}</code>"
    )
    if status_line:
        return f"{text}\n\n{status_line}"
    return f"{text}\n\nПроверьте поступление средств и выберите действие:"

def user_order_text(order: dict, result: str = "") -> str:
    """Сообщение покупателю о его заявке: «на проверке», затем «выполнена» или «отклонена»."""
    head = {
        "approved": f"✅ <b>Заявка №{order['id']} выполнена</b>",
        "rejected": f"❌ <b>Заявка №{order['id']} отклонена</b>",
    }.get(order["status"], f"⏳ <b>Заявка №{order['id']} на проверке</b>")
    lines = [head, "", f"{_days_title(order['days'])} — {order['price']} ₽"]
    if order["kind"] == "extend":
        title = get_sub_labels(order["tg_id"]).get(order["target_client_id"]) or order["target_client_name"] or ""
        lines.append(f"Продление «{html.escape(title)}»")
    elif panel_api:
        lines.append("Новая подписка")
    lines.append("")
    lines.append(result or "Обычно проверка занимает 5–15 минут. Как только оплату подтвердят, "
                           "это сообщение обновится, а бот пришлёт ссылку для подключения.")
    return "\n".join(lines)

async def update_user_order_msg(order: dict, result: str):
    if not order or not order.get("user_msg_id"):
        return
    try:
        await bot.edit_message_text(user_order_text(order, result), chat_id=order["tg_id"],
                                    message_id=order["user_msg_id"], parse_mode="HTML")
    except Exception as e:
        logging.info(f"Не удалось обновить статус заявки №{order['id']} у покупателя: {e}")

def order_keyboard(order_id: int, retry: bool = False) -> InlineKeyboardMarkup:
    approve = (InlineKeyboardButton(text="🔁 Повторить", callback_data=f"retry:{order_id}") if retry
               else InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"approve:{order_id}"))
    return InlineKeyboardMarkup(inline_keyboard=[[
        approve,
        InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{order_id}"),
    ]])

@dp.message(BuySubscriptionState.waiting_for_receipt, F.photo | F.document)
async def handle_receipt_media(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    username = message.from_user.username
    full_name = message.from_user.full_name

    data = await state.get_data()
    order_id = None
    tariff_code = data.get("tariff_code")
    tariff = TARIFFS.get(tariff_code)
    await state.clear()
    if not tariff:
        await message.answer("Тариф больше не действует, выберите его заново через «Купить подписку».")
        return

    try:
        order_id = create_order(user_id, username, full_name, tariff_code, tariff["days"], tariff["price"],
                                data.get("target_client_id"), data.get("target_client_name"))
        order = get_order(order_id)
        caption = order_caption(order)
        admin_keyboard = order_keyboard(order_id)
    except Exception as e:
        # Чек не теряем: админ получит его без кнопок и выдаст подписку руками
        logging.error(f"Не удалось сохранить заявку: {e}")
        user_mention = f"@{username}" if username else f"ID: {user_id}"
        caption = (
            f"💳 <b>Новая заявка на оплату</b> (⚠️ не сохранилась в базе бота, выдайте вручную)\n\n"
            f"🗓 Тариф: <b>{tariff['title']} — {tariff['price']} ₽</b>\n"
            f"👤 Пользователь: {html.escape(full_name)} ({html.escape(user_mention)})\n"
            f"🆔 Telegram ID: <code>{user_id}</code>"
        )
        admin_keyboard = None

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
            [InlineKeyboardButton(text="← В главное меню", callback_data="main_menu")]
        ]
    )
    if order_id:
        status_msg = await message.answer(user_order_text(get_order(order_id)), reply_markup=user_keyboard,
                                          parse_mode="HTML")
        set_order_user_msg(order_id, status_msg.message_id)
    else:
        await message.answer(
            "⏳ <b>Чек принят на проверку</b>\n\n"
            f"{tariff['title']} — {tariff['price']} ₽\n"
            "Обычно проверка занимает 5–15 минут. Как только оплату подтвердят, бот пришлёт ссылку для подключения.",
            reply_markup=user_keyboard,
            parse_mode="HTML"
        )

@dp.message(BuySubscriptionState.waiting_for_receipt)
async def handle_receipt_invalid(message: types.Message):
    cancel_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Отмена", callback_data="main_menu")]
        ]
    )
    await message.answer(
        "<b>Пришлите, пожалуйста, скриншот чека — фото или файлом.</b>\n\n"
        "Если передумали, нажмите «Отмена».",
        reply_markup=cancel_keyboard,
        parse_mode="HTML"
    )

# Кнопки approve_sub:/reject_sub: — у чеков, пришедших до появления заявок.
# Они только уведомляют пользователя, подписку по ним админ выдаёт вручную.
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
            new_caption = callback.message.html_text + "\n\n<b>Статус:</b> ✅ ОДОБРЕНО"
            await callback.message.edit_caption(caption=new_caption, reply_markup=None, parse_mode="HTML")
        else:
            new_text = callback.message.html_text + "\n\n<b>Статус:</b> ✅ ОДОБРЕНО"
            await callback.message.edit_text(text=new_text, reply_markup=None, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Ошибка редактирования сообщения админа: {e}")

    user_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📦 Ваша подписка", callback_data="my_subscription")],
            [InlineKeyboardButton(text="← Главное меню", callback_data="main_menu")],
        ]
    )
    try:
        await bot.send_message(
            target_user_id,
            f"🎉 <b>Оплата подтверждена</b>\n\n"
            f"Подписка на <b>{tariff_title}</b> успешно активирована.\n"
            "Нажмите <b>«Ваша подписка»</b>, чтобы получить ссылку для подключения.",
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
            new_caption = callback.message.html_text + "\n\n<b>Статус:</b> ❌ ОТКЛОНЕНО"
            await callback.message.edit_caption(caption=new_caption, reply_markup=None, parse_mode="HTML")
        else:
            new_text = callback.message.html_text + "\n\n<b>Статус:</b> ❌ ОТКЛОНЕНО"
            await callback.message.edit_text(text=new_text, reply_markup=None, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Ошибка редактирования сообщения админа: {e}")

    try:
        await bot.send_message(
            target_user_id,
            "❌ <b>Оплата не подтверждена</b>\n\n"
            "К сожалению, администратор не подтвердил получение перевода по вашему чеку.\n"
            "Если у вас есть вопросы или произошла ошибка, свяжитесь с поддержкой.",
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление пользователю {target_user_id}: {e}")

async def set_admin_status(message: types.Message, text: str, keyboard: InlineKeyboardMarkup | None = None):
    """Обновляет подпись к чеку (или текст) в чате админа."""
    try:
        if message.caption is not None:
            await message.edit_caption(caption=text, reply_markup=keyboard, parse_mode="HTML")
        else:
            await message.edit_text(text=text, reply_markup=keyboard, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Ошибка редактирования сообщения админа: {e}")

def _date_str(expiry: int) -> str:
    return datetime.fromtimestamp(expiry).strftime("%d.%m.%Y") if expiry > 0 else "бессрочно"

@dp.callback_query(F.data.startswith(("approve:", "retry:")))
async def cb_admin_approve_order(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_TG_ID:
        await callback.answer("⛔ У вас нет прав для этого действия!", show_alert=True)
        return

    order_id = int(callback.data.split(":")[1])
    order = get_order(order_id)
    if not order:
        await callback.answer("Заявка не найдена", show_alert=True)
        return

    # Без API — как раньше: подтверждаем оплату, подписку админ создаёт в панели сам
    if not panel_api:
        if not claim_order(order_id, ("pending", "failed"), "approved"):
            await callback.answer("Заявка уже обработана", show_alert=True)
            return
        await callback.answer("Оплата подтверждена ✅")
        await set_admin_status(callback.message, order_caption(
            order, "<b>Статус:</b> ✅ ОДОБРЕНО\n<i>API панели не настроено: создайте подписку в панели вручную.</i>"))
        await update_user_order_msg(get_order(order_id), "Оплата подтверждена.")
        await notify_user_manual_approve(order)
        return

    # Двойное нажатие или повтор с другого устройства не выдаст вторую подписку
    if not claim_order(order_id, ("pending", "failed"), "processing"):
        await callback.answer("Заявка уже обработана", show_alert=True)
        return
    await callback.answer("Выдаю подписку…")
    await set_admin_status(callback.message, order_caption(order, "<b>Статус:</b> ⏳ выдаю подписку…"))

    try:
        if order["kind"] == "extend":
            client = await panel_api.extend(order["target_client_id"], order["days"], order["tg_id"])
        else:
            client = await panel_api.create(order["tg_id"], order["days"])
    except Exception as e:
        error = str(e) or e.__class__.__name__
        if not isinstance(e, PanelAPIError):
            logging.exception(f"Ошибка выдачи по заявке №{order_id}")
        finish_order(order_id, "failed", error=error)
        await set_admin_status(
            callback.message,
            order_caption(order, f"<b>Статус:</b> ⚠️ Ошибка выдачи: {html.escape(error)}"),
            order_keyboard(order_id, retry=True),
        )
        return

    name = client["name"]
    expiry = expiry_seconds(client.get("expiry"))
    finish_order(order_id, "approved", client_id=client.get("id"), client_name=name, expiry=expiry)
    await set_admin_status(callback.message, order_caption(
        order, f"<b>Статус:</b> ✅ ВЫДАНО: <code>{html.escape(name)}</code> до {_date_str(expiry)}"))

    action = "продлена" if order["kind"] == "extend" else "активирована"
    title = get_sub_labels(order["tg_id"]).get(client.get("id")) or name
    await update_user_order_msg(get_order(order_id), f"Подписка «{html.escape(title)}» {action} до <b>{_date_str(expiry)}</b>.")
    rows = [[InlineKeyboardButton(text="🚀 Подключиться", web_app=WebAppInfo(url=dashboard_url(name)))]]
    if order["kind"] != "extend" and client.get("id"):
        rows.append([InlineKeyboardButton(text="Дать название", callback_data=f"rename:{client['id']}"),
                     InlineKeyboardButton(text="Мои подписки", callback_data="my_subscription")])
    else:
        rows.append([InlineKeyboardButton(text="Мои подписки", callback_data="my_subscription")])
    try:
        await bot.send_message(
            order["tg_id"],
            "🎉 <b>Оплата подтверждена</b>\n\n"
            f"Подписка <b>«{html.escape(title)}»</b> {action} до <b>{_date_str(expiry)}</b>.\n\n"
            "Нажмите <b>«🚀 Подключиться»</b> — откроется страница с инструкцией и импортом в приложение в одно касание.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление пользователю {order['tg_id']}: {e}")

async def notify_user_manual_approve(order: dict):
    user_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📦 Ваша подписка", callback_data="my_subscription")],
            [InlineKeyboardButton(text="← Главное меню", callback_data="main_menu")],
        ]
    )
    try:
        await bot.send_message(
            order["tg_id"],
            f"🎉 <b>Оплата подтверждена</b>\n\n"
            f"Подписка на <b>{_days_title(order['days'])}</b> успешно активирована.\n"
            "Нажмите <b>«Ваша подписка»</b>, чтобы получить ссылку для подключения.",
            reply_markup=user_keyboard,
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление пользователю {order['tg_id']}: {e}")

@dp.callback_query(F.data.startswith("reject:"))
async def cb_admin_reject_order(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_TG_ID:
        await callback.answer("⛔ У вас нет прав для этого действия!", show_alert=True)
        return

    order_id = int(callback.data.split(":")[1])
    order = get_order(order_id)
    if not order:
        await callback.answer("Заявка не найдена", show_alert=True)
        return
    if not claim_order(order_id, ("pending", "failed"), "rejected"):
        await callback.answer("Заявка уже обработана", show_alert=True)
        return
    await callback.answer("Заявка отклонена ❌")
    await set_admin_status(callback.message, order_caption(order, "<b>Статус:</b> ❌ ОТКЛОНЕНО"))
    await update_user_order_msg(get_order(order_id), "Оплата не подтверждена.")

    try:
        await bot.send_message(
            order["tg_id"],
            "❌ <b>Оплата не подтверждена</b>\n\n"
            "Администратор не нашёл перевод по вашему чеку.\n"
            "Если это ошибка — напишите в поддержку, приложив чек.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Поддержка", url=f"https://t.me/{SUPPORT_BOT_USERNAME}")],
                [InlineKeyboardButton(text="← Главное меню", callback_data="main_menu")],
            ]),
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление пользователю {order['tg_id']}: {e}")

LABEL_PRESETS = ["Телефон", "Ноутбук", "Компьютер", "Планшет", "Телевизор", "Семья"]
LABEL_MAX_LEN = 24

GROUP_NOTE = "📢 Новости и важные объявления сервиса публикуются в нашей группе — кнопка ниже."

def group_button() -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text=SERVICE_GROUP_NAME, url=SERVICE_GROUP_URL)]

def dashboard_url(name: str) -> str:
    return f"https://{SUB_DOMAIN}/{quote(name)}"

def sub_title(sub: dict) -> str:
    """Название подписки для пользователя: своё, если задано, иначе имя из панели."""
    return sub.get("label") or sub["name"]

def _gb(b: int) -> str:
    gb = b / 1024 ** 3
    return f"{gb:.1f}".rstrip("0").rstrip(".") if gb < 10 else f"{gb:.0f}"

def traffic_bar(used: int, volume: int, width: int = 10) -> str:
    filled = min(width, round(width * used / volume)) if volume > 0 else 0
    return "▓" * filled + "░" * (width - filled)

def sub_state(sub: dict) -> str:
    """Статус подписки словом, по тем же правилам, что цвет на дашборде."""
    now = time.time()
    exp, volume, used = sub["expiry_ts"], sub["volume"], sub["used"]
    if exp > 0 and exp <= now:
        return "истекла"
    if volume > 0 and used >= volume:
        return "трафик закончился"
    if not sub["enabled"]:
        return "отключена"
    if exp > 0 and exp - now <= 5 * 86400:
        return "скоро закончится"
    if volume > 0 and used >= 0.9 * volume:
        return "трафик на исходе"
    return "активна"

def _days_left(sub: dict) -> int:
    return max(0, math.ceil((sub["expiry_ts"] - time.time()) / 86400))

def sub_short_status(sub: dict) -> str:
    """Коротко для кнопок: «23 дн.», «истекла», «бессрочно»."""
    if sub["expiry_ts"] == 0:
        return "бессрочно"
    if sub["expiry_ts"] <= time.time():
        return "истекла"
    return f"{_days_left(sub)} дн."

def sub_card(sub: dict, many: bool) -> tuple[str, InlineKeyboardMarkup]:
    lines = [f"<b>{html.escape(sub_title(sub))}</b> — {sub_state(sub)}"]
    if sub.get("label"):
        lines.append(f"<i>Подписка {html.escape(sub['name'])}</i>")
    lines.append("")

    exp = sub["expiry_ts"]
    if exp == 0:
        lines.append("Бессрочная подписка")
    elif exp > time.time():
        lines.append(f"Действует до <b>{_date_str(exp)}</b>, осталось <b>{_days_title(_days_left(sub))}</b>")
    else:
        lines.append(f"Закончилась <b>{_date_str(exp)}</b>")

    if sub["volume"] > 0:
        lines.append(f"Трафик: <code>{traffic_bar(sub['used'], sub['volume'])}</code> "
                     f"{_gb(sub['used'])} / {_gb(sub['volume'])} ГБ")
    else:
        lines.append(f"Трафик: {_gb(sub['used'])} ГБ, без лимита")
    if sub.get("reset_str"):
        lines.append(f"Трафик обновится <b>{sub['reset_str']}</b>")
    lines.append(cap(devices_text(sub["limit_ip"])))
    lines += ["", "<i>Подключение и инструкции — по кнопке «🚀 Подключиться».</i>"]
    if not many:
        lines += ["", GROUP_NOTE]

    url = dashboard_url(sub["name"])
    buttons = [
        [InlineKeyboardButton(text="🚀 Подключиться", web_app=WebAppInfo(url=url))],
        [InlineKeyboardButton(text="💳 Продлить", callback_data=f"renew:{sub['id']}"),
         InlineKeyboardButton(text="Переименовать", callback_data=f"rename:{sub['id']}")],
        [InlineKeyboardButton(text="Открыть в браузере", url=url)],
    ]
    if many:
        buttons.append([InlineKeyboardButton(text="← Все подписки", callback_data="my_subscription")])
    else:
        buttons.append(group_button())
        buttons.append([InlineKeyboardButton(text="← Главное меню", callback_data="main_menu")])
    return "\n".join(lines), InlineKeyboardMarkup(inline_keyboard=buttons)

def subs_list_screen(subs: list[dict]) -> tuple[str, InlineKeyboardMarkup]:
    buttons = [
        [InlineKeyboardButton(text=f"{sub_title(s)} · {sub_short_status(s)}", callback_data=f"sub:{s['id']}")]
        for s in subs
    ]
    buttons.append([InlineKeyboardButton(text="Ещё одна подписка", callback_data="buy_subscription")])
    buttons.append(group_button())
    buttons.append([InlineKeyboardButton(text="← Главное меню", callback_data="main_menu")])
    text = (
        f"📦 <b>Ваши подписки: {len(subs)}</b>\n\n"
        "Выберите подписку, чтобы посмотреть срок и трафик, продлить её или подключиться.\n\n"
        f"{GROUP_NOTE}"
    )
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)

def no_subs_screen(tg_id: int) -> tuple[str, InlineKeyboardMarkup]:
    pending = get_pending_order(tg_id)
    if pending:
        status = (f"⏳ <b>Ваша заявка №{pending['id']} на проверке</b>\n\n"
                  "Как только оплату подтвердят, бот пришлёт ссылку для подключения.")
    else:
        status = ("🔒 <b>У вас пока нет подписки</b>\n\n"
                  "Оформите её за пару минут: выберите срок, оплатите переводом и пришлите чек — "
                  "ссылка для подключения придёт сюда же.")
    text = f"{status}\n\n{cap(devices_text())}, {traffic_text()}.\nHysteria2, TUIC, VLESS."
    buttons = [] if pending else [[InlineKeyboardButton(text="💳 Купить подписку", callback_data="buy_subscription")]]
    buttons += [
        [InlineKeyboardButton(text="Поддержка", url=f"https://t.me/{SUPPORT_BOT_USERNAME}")],
        [InlineKeyboardButton(text="← Главное меню", callback_data="main_menu")],
    ]
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)

def find_own_subscription(tg_id: int, client_id) -> dict | None:
    """Подписка пользователя по id; чужую не вернёт."""
    return next((s for s in find_subscriptions_by_tg_id(str(tg_id)) if str(s["id"]) == str(client_id)), None)

@dp.callback_query(F.data == "my_subscription")
async def cb_my_subscription(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.answer()
    subs = find_subscriptions_by_tg_id(str(callback.from_user.id))
    if not subs:
        text, keyboard = no_subs_screen(callback.from_user.id)
    elif len(subs) == 1:
        text, keyboard = sub_card(subs[0], many=False)
    else:
        text, keyboard = subs_list_screen(subs)
    await show_screen(callback, text, keyboard)

@dp.callback_query(F.data.startswith("sub:"))
async def cb_sub_card(callback: types.CallbackQuery, state: FSMContext):
    await state.clear()
    subs = find_subscriptions_by_tg_id(str(callback.from_user.id))
    sub = next((s for s in subs if str(s["id"]) == callback.data.split(":")[1]), None)
    if not sub:
        await callback.answer("Подписка не найдена", show_alert=True)
        return
    await callback.answer()
    text, keyboard = sub_card(sub, many=len(subs) > 1)
    await show_screen(callback, text, keyboard)

@dp.callback_query(F.data.startswith("rename:"))
async def cb_rename(callback: types.CallbackQuery, state: FSMContext):
    sub = find_own_subscription(callback.from_user.id, callback.data.split(":")[1])
    if not sub:
        await callback.answer("Подписка не найдена", show_alert=True)
        return
    await state.set_state(RenameState.waiting_for_label)
    await state.update_data(rename_id=sub["id"])
    await callback.answer()
    presets = [InlineKeyboardButton(text=p, callback_data=f"label:{sub['id']}:{i}") for i, p in enumerate(LABEL_PRESETS)]
    buttons = [presets[i:i + 2] for i in range(0, len(presets), 2)]
    if sub.get("label"):
        buttons.append([InlineKeyboardButton(text="Убрать название", callback_data=f"label:{sub['id']}:clear")])
    buttons.append([InlineKeyboardButton(text="Отмена", callback_data=f"sub:{sub['id']}")])
    text = (
        f"✏️ <b>Название для подписки {html.escape(sub['name'])}</b>\n\n"
        "Выберите вариант ниже или пришлите своё название сообщением "
        f"(до {LABEL_MAX_LEN} символов). Оно видно только вам, ссылка для подключения не изменится."
    )
    await show_screen(callback, text, InlineKeyboardMarkup(inline_keyboard=buttons))

@dp.callback_query(F.data.startswith("label:"))
async def cb_label_preset(callback: types.CallbackQuery, state: FSMContext):
    _, client_id, choice = callback.data.split(":")
    sub = find_own_subscription(callback.from_user.id, client_id)
    if not sub or (choice != "clear" and not (choice.isdigit() and int(choice) < len(LABEL_PRESETS))):
        await callback.answer("Подписка не найдена", show_alert=True)
        return
    label = None if choice == "clear" else LABEL_PRESETS[int(choice)]
    set_sub_label(sub["id"], callback.from_user.id, label)
    await state.clear()
    await callback.answer("Название сохранено" if label else "Название убрано")
    subs = find_subscriptions_by_tg_id(str(callback.from_user.id))
    sub = next(s for s in subs if s["id"] == sub["id"])
    text, keyboard = sub_card(sub, many=len(subs) > 1)
    await show_screen(callback, text, keyboard)

@dp.message(RenameState.waiting_for_label, F.text)
async def handle_label_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    sub = find_own_subscription(message.from_user.id, data.get("rename_id"))
    if not sub:
        await state.clear()
        await message.answer("Подписка не найдена.")
        return
    label = " ".join(message.text.split())
    if not label or len(label) > LABEL_MAX_LEN:
        await message.answer(f"Название должно быть от 1 до {LABEL_MAX_LEN} символов. Попробуйте ещё раз:")
        return
    set_sub_label(sub["id"], message.from_user.id, label)
    await state.clear()
    subs = find_subscriptions_by_tg_id(str(message.from_user.id))
    sub = next(s for s in subs if s["id"] == sub["id"])
    text, keyboard = sub_card(sub, many=len(subs) > 1)
    await message.answer(f"Название сохранено.\n\n{text}", reply_markup=keyboard, parse_mode="HTML")

@dp.message(RenameState.waiting_for_label)
async def handle_label_invalid(message: types.Message):
    await message.answer("Пришлите название текстом или выберите вариант кнопкой выше.")

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
        # Заявки на оплату: что купили, кто подтвердил и какая подписка выдана
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tg_id INTEGER NOT NULL,
                username TEXT,
                full_name TEXT,
                tariff_code TEXT NOT NULL,
                days INTEGER NOT NULL,
                price INTEGER NOT NULL,
                kind TEXT NOT NULL,
                target_client_id INTEGER,
                target_client_name TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                client_id INTEGER,
                client_name TEXT,
                expiry INTEGER,
                error TEXT,
                created_at INTEGER NOT NULL,
                decided_at INTEGER
            )
        """)
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_orders_tg_id ON orders(tg_id)")
        # Сообщение покупателю со статусом заявки, которое бот обновляет после решения
        if "user_msg_id" not in {r[1] for r in cursor.execute("PRAGMA table_info(orders)")}:
            cursor.execute("ALTER TABLE orders ADD COLUMN user_msg_id INTEGER")
        # Свои названия подписок («Телефон»), видны только владельцу
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sub_labels (
                client_id INTEGER PRIMARY KEY,
                tg_id INTEGER NOT NULL,
                label TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        # Заявка, на которой бот перезапустился посреди выдачи: даём админу повторить,
        # но предупреждаем, что подписка могла успеть создаться
        cursor.execute("""
            UPDATE orders SET status = 'failed',
                error = 'выдача прервана перезапуском бота, проверьте панель перед повтором'
            WHERE status = 'processing'
        """)
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

def create_order(tg_id: int, username: str | None, full_name: str, tariff_code: str, days: int, price: int,
                 target_client_id: int | None, target_client_name: str | None) -> int:
    """Сохраняет заявку на оплату и возвращает её номер."""
    conn = sqlite3.connect(BOT_DATA_DB)
    try:
        cursor = conn.execute("""
            INSERT INTO orders (tg_id, username, full_name, tariff_code, days, price, kind,
                                target_client_id, target_client_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (tg_id, username, full_name, tariff_code, days, price,
              "extend" if target_client_id else "new", target_client_id, target_client_name, int(time.time())))
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()

def get_order(order_id: int) -> dict | None:
    conn = sqlite3.connect(BOT_DATA_DB)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM orders WHERE id = ?", (order_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()

def claim_order(order_id: int, from_statuses: tuple[str, ...], to_status: str) -> bool:
    """Атомарно переводит заявку в новый статус. False — её уже обработали (двойное нажатие)."""
    conn = sqlite3.connect(BOT_DATA_DB)
    try:
        marks = ",".join("?" * len(from_statuses))
        cursor = conn.execute(
            f"UPDATE orders SET status = ?, decided_at = ? WHERE id = ? AND status IN ({marks})",
            (to_status, int(time.time()), order_id, *from_statuses),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()

def finish_order(order_id: int, status: str, client_id: int | None = None, client_name: str | None = None,
                 expiry: int | None = None, error: str | None = None):
    conn = sqlite3.connect(BOT_DATA_DB)
    try:
        conn.execute("""
            UPDATE orders SET status = ?, client_id = ?, client_name = ?, expiry = ?, error = ?, decided_at = ?
            WHERE id = ?
        """, (status, client_id, client_name, expiry, error, int(time.time()), order_id))
        conn.commit()
    finally:
        conn.close()

def set_order_user_msg(order_id: int, message_id: int):
    conn = sqlite3.connect(BOT_DATA_DB)
    try:
        conn.execute("UPDATE orders SET user_msg_id = ? WHERE id = ?", (message_id, order_id))
        conn.commit()
    finally:
        conn.close()

def get_pending_order(tg_id: int) -> dict | None:
    """Последняя заявка пользователя, ожидающая решения админа."""
    try:
        conn = sqlite3.connect(BOT_DATA_DB)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute("""
                SELECT * FROM orders WHERE tg_id = ? AND status IN ('pending', 'processing', 'failed')
                ORDER BY id DESC LIMIT 1
            """, (tg_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    except Exception as e:
        logging.error(f"Ошибка чтения заявок: {e}")
        return None

def get_sub_labels(tg_id: int | None = None) -> dict[int, str]:
    """Свои названия подписок: {client_id: название}, для пользователя или для всех."""
    try:
        conn = sqlite3.connect(BOT_DATA_DB)
        try:
            if tg_id is None:
                rows = conn.execute("SELECT client_id, label FROM sub_labels").fetchall()
            else:
                rows = conn.execute("SELECT client_id, label FROM sub_labels WHERE tg_id = ?", (tg_id,)).fetchall()
            return dict(rows)
        finally:
            conn.close()
    except Exception as e:
        logging.error(f"Ошибка чтения названий подписок: {e}")
        return {}

def set_sub_label(client_id: int, tg_id: int, label: str | None):
    conn = sqlite3.connect(BOT_DATA_DB)
    try:
        if label:
            conn.execute("""
                INSERT INTO sub_labels (client_id, tg_id, label, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(client_id) DO UPDATE SET tg_id = excluded.tg_id, label = excluded.label,
                                                     updated_at = excluded.updated_at
            """, (client_id, tg_id, label, int(time.time())))
        else:
            conn.execute("DELETE FROM sub_labels WHERE client_id = ?", (client_id,))
        conn.commit()
    finally:
        conn.close()

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

def _sub_buttons(client_id: int, renew_text: str, support: bool) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=renew_text, callback_data=f"renew:{client_id}")],
        [InlineKeyboardButton(text="Моя подписка", callback_data=f"sub:{client_id}")],
    ]
    if support:
        rows.append([InlineKeyboardButton(text="Поддержка", url=f"https://t.me/{SUPPORT_BOT_USERNAME}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def expiry_notification(notif_type: str, client_id: int, title: str, expiry_ts: int) -> tuple[str, InlineKeyboardMarkup]:
    """Напоминание об окончании срока: кнопка продления ведёт сразу на продление этой подписки."""
    safe = html.escape(title)
    expiry_dt = datetime.fromtimestamp(expiry_ts)
    date_str = expiry_dt.strftime("%d.%m.%Y")
    if notif_type == "7d":
        text = (
            f"⏳ <b>Подписка «{safe}» закончится через 7 дней</b>\n\n"
            f"Действует до <b>{date_str}</b>.\n"
            "Продлите заранее — новые дни прибавятся к текущему сроку, ссылка не изменится."
        )
        return text, _sub_buttons(client_id, "💳 Продлить подписку", support=False)
    if notif_type == "3d":
        text = (
            f"⚠️ <b>До окончания подписки «{safe}» осталось 3 дня</b>\n\n"
            f"Действует до <b>{date_str}</b>.\n"
            "Продлите сейчас, чтобы интернет работал без перерыва. Это займёт минуту."
        )
        return text, _sub_buttons(client_id, "💳 Продлить сейчас", support=True)
    if notif_type == "12h":
        text = (
            f"🚨 <b>Подписка «{safe}» закончится меньше чем через 12 часов</b>\n\n"
            f"Отключение: <b>{date_str} в {expiry_dt.strftime('%H:%M')}</b>.\n"
            "Продлите сейчас, чтобы устройства не отключились."
        )
        return text, _sub_buttons(client_id, "💳 Продлить", support=True)
    text = (
        f"❌ <b>Подписка «{safe}» закончилась</b>\n\n"
        f"Срок истёк <b>{date_str}</b>, подключение приостановлено.\n"
        "Возобновить можно в любой момент — ссылка останется прежней."
    )
    return text, _sub_buttons(client_id, "💳 Возобновить подписку", support=True)

def traffic_notification(notif_type: str, client_id: int, title: str, used: int, volume: int,
                         reset_ts: int) -> tuple[str, InlineKeyboardMarkup]:
    safe = html.escape(title)
    reset_line = (f"Трафик обновится <b>{_date_str(reset_ts)}</b>." if reset_ts > 0
                  else "Чтобы добавить трафик, напишите в поддержку.")
    if notif_type == "traffic90":
        text = (
            f"📊 <b>Трафик по подписке «{safe}» на исходе</b>\n\n"
            f"Израсходовано <b>{round(100 * used / volume)}%</b>: {_gb(used)} из {_gb(volume)} ГБ.\n"
            f"{reset_line}"
        )
    else:
        text = (
            f"🚫 <b>Трафик по подписке «{safe}» закончился</b>\n\n"
            f"Израсходованы все {_gb(volume)} ГБ, подключение приостановлено.\n"
            + (f"Трафик обновится <b>{_date_str(reset_ts)}</b>, и подписка включится автоматически."
               if reset_ts > 0 else reset_line)
        )
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Моя подписка", callback_data=f"sub:{client_id}")],
        [InlineKeyboardButton(text="Поддержка", url=f"https://t.me/{SUPPORT_BOT_USERNAME}")],
    ])
    return text, keyboard

async def _send_notification(tg_id: int, name: str, notif_type: str, period_ts: int,
                             text: str, keyboard: InlineKeyboardMarkup):
    try:
        await bot.send_message(tg_id, text, reply_markup=keyboard, parse_mode="HTML")
        logging.info(f"Успешно отправлено уведомление [{notif_type}] клиенту {name} (TG ID: {tg_id})")
    except Exception as e:
        logging.warning(f"Не удалось отправить уведомление [{notif_type}] пользователю {tg_id} ({name}): {e}")
    # Фиксируем отправку в БД истории
    record_notification_sent(name, tg_id, notif_type, period_ts)
    # Небольшая пауза между отправками (антиспам Telegram)
    await asyncio.sleep(0.3)

async def check_and_send_expiry_notifications():
    """Напоминания: срок (за 7 дней, 3 дня, 12 часов, при истечении) и трафик (90% и 100%)."""
    if not os.path.exists(DB_PATH):
        return

    try:
        conn = connect_panel_db()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, name, desc, remark, tg_id, expiry, enable, volume, up, down, auto_reset, next_reset
            FROM clients
        """)
        clients = cursor.fetchall()
        conn.close()
    except Exception as e:
        logging.error(f"Ошибка выборки клиентов из s-ui.db: {e}")
        return

    now_ts = int(time.time())
    labels = get_sub_labels()

    for (client_id, name, desc, remark, tg_id_raw, expiry_raw, enable, volume,
         up, down, auto_reset, next_reset) in clients:
        tg_id = extract_tg_id(desc, remark, tg_id_raw)
        if not tg_id:
            continue
        enabled = bool(enable)
        title = labels.get(client_id) or name
        expiry_ts = expiry_seconds(expiry_raw)
        expired = 0 < expiry_ts <= now_ts

        # Трафик: только у действующих по сроку подписок с лимитом. Период уведомления —
        # дата следующего сброса, чтобы в новом месяце предупредить снова.
        volume = int(volume or 0)
        if volume > 0 and not expired:
            used = int(up or 0) + int(down or 0)
            reset_ts = expiry_seconds(next_reset) if auto_reset else 0
            traffic_type = None
            if used >= volume:
                traffic_type = "traffic100"
            elif used >= 0.9 * volume and enabled:
                traffic_type = "traffic90"
            period = reset_ts or expiry_ts
            if traffic_type and not is_notification_sent(name, traffic_type, period):
                text, keyboard = traffic_notification(traffic_type, client_id, title, used, volume, reset_ts)
                await _send_notification(tg_id, name, traffic_type, period, text, keyboard)

        if expiry_ts <= 0:
            continue
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

        # Панель отключает клиента сразу по истечении срока, поэтому для "expired"
        # отключенных не пропускаем; остальные напоминания — только активным.
        if not enabled and notif_type != "expired":
            continue

        # Проверяем, не отправляли ли уже такое уведомление для текущего периода
        if is_notification_sent(name, notif_type, expiry_ts):
            continue

        text, keyboard = expiry_notification(notif_type, client_id, title, expiry_ts)
        await _send_notification(tg_id, name, notif_type, expiry_ts, text, keyboard)

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
        conn = connect_panel_db()
        cursor = conn.cursor()

        # LIKE отбирает только кандидатов, точная проверка ниже через extract_tg_id
        query = """
            SELECT id, name, expiry, auto_reset, next_reset, desc, remark, tg_id,
                   enable, volume, up, down, limit_ip
            FROM clients
            WHERE desc LIKE ? OR remark LIKE ? OR tg_id = ?
            ORDER BY id
        """
        tg_num = int(telegram_id) if str(telegram_id).isdigit() else 0
        cursor.execute(query, (f"%{telegram_id}%", f"%{telegram_id}%", tg_num))
        rows = cursor.fetchall()
        conn.close()
        labels = get_sub_labels(tg_num)

        subs = []
        for (client_id, name, expiry, auto_reset, next_reset, desc, remark, tg_id_raw,
             enable, volume, up, down, limit_ip) in rows:
            if extract_tg_id(desc, remark, tg_id_raw) != tg_num:
                continue
            expiry_ts = expiry_seconds(expiry)
            date_str = _date_str(expiry_ts) if expiry_ts > 0 else "Бессрочно"

            reset_str = None
            if auto_reset and next_reset and next_reset > 0:
                reset_str = _date_str(expiry_seconds(next_reset))

            subs.append({
                "id": client_id,
                "name": name,
                "label": labels.get(client_id),
                "expiry": expiry,
                "expiry_ts": expiry_ts,
                "date_str": date_str,
                "auto_reset": bool(auto_reset),
                "next_reset": next_reset,
                "reset_str": reset_str,
                "enabled": bool(enable),
                "volume": int(volume or 0),
                "used": int(up or 0) + int(down or 0),
                "limit_ip": int(limit_ip or 0),
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
        conn = connect_panel_db()
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
        conn = connect_panel_db()
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
            [InlineKeyboardButton(text="← В клиентское меню", callback_data="main_menu")],
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
            [InlineKeyboardButton(text="← В главное меню", callback_data="main_menu")],
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

