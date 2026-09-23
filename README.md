# 🚀 VPN Suite: Telegram Bot & Web Dashboard (Единая директория)

[![OS](https://img.shields.io/badge/OS-Ubuntu%20%7C%20Debian-orange.svg)](https://ubuntu.com/)
[![Python](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Aiogram](https://img.shields.io/badge/Aiogram-3.x-green.svg)](https://docs.aiogram.dev/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![Nginx](https://img.shields.io/badge/Nginx-Reverse%20Proxy-brightgreen.svg)](https://nginx.org/)
[![SSL](https://img.shields.io/badge/SSL-Let's%20Encrypt%20(Certbot)-yellow.svg)](https://certbot.eff.org/)

Объединенный комплекс сервисов для управления коммерческими и приватными VPN-подписками на базе панели **2S-UI** (или **X-UI** с базой данных SQLite).

Все сервисы объединены в **единую директорию**, используют общее виртуальное окружение Python (`venv`), общий файл конфигурации `.env` и управляются двумя независимыми системными службами `systemd`.

---

## 📦 Что входит в комплекс

* 🤖 **`bot.py` (Telegram Bot на Aiogram 3)**:
  * Проверка привязки Telegram ID к клиентам в SQLite базе панели.
  * Витрина тарифных планов и реквизитов для оплаты с копированием в 1 тап.
  * Прием чеков об оплате и интерактивная модерация администратором (`✅ Одобрить` / `❌ Отклонить`).
  * Фоновый воркер уведомлений за 7 дней, 3 дня, 12 часов и при окончании подписки.
  * Сервисное меню администратора (`/admin`) и умные рассылки с автоудалением по таймеру (TTL).
* 🌐 **`app.py` и `index.html` (Веб-дашборд на FastAPI)**:
  * Быстрая валидация существования подписки клиента.
  * Современный адаптивный лендинг с отображением остатка дней и расхода трафика.
  * Кнопки подключения подписки в один клик для приложений (Hiddify, Incy, Happ, Streisand, v2rayN и др.).
  * Кнопки перехода в Telegram-бота и чат саппорта.
* 🛡 **`Nginx Reverse Proxy`**:
  * Порт 443 (HTTPS) с автоматическим выпуском сертификата Let's Encrypt.
  * Запросы на корень `/` отдаются через локальный FastAPI (порт 8000).
  * Запросы к сырым конфигурациям подписок `/sub/` безопасно проксируются на локальный порт панели (2096).

---

## ⚡️ Быстрая установка на сервер

### 1. Требования
* Сервер с ОС **Ubuntu 20.04+** или **Debian 11+**.
* Установленная панель **2S-UI** (или X-UI) с файлом SQLite БД (`/usr/local/s-ui/db/s-ui.db`).
* Поддомен (например, `sub.example.com`), направленный на IP вашего сервера (A-запись в DNS).

### 2. Запуск авто-установщика
Клонируйте репозиторий и запустите установщик:

```bash
git clone https://github.com/ВАШ_АККАУНТ/vpn-service.git /root/vpn-service
cd /root/vpn-service
sudo bash install.sh
```

Скрипт запросит основные параметры (домен, токен бота, ID админа, контакты поддержки) и выполнит полную установку «под ключ»:
1. Установит необходимые пакеты системы (`nginx`, `python3-venv`, `certbot` и др.).
2. Развернет файлы проекта в `/var/www/vpn-service`.
3. Создаст общее виртуальное окружение `venv` и установит `requirements.txt`.
4. Сгенерирует `.env` с безопасными правами доступа (`600`).
5. Настроит виртуальный хост Nginx и выпустит бесплатный SSL-сертификат Let's Encrypt.
6. Зарегистрирует и запустит обе службы в systemd.

---

## 📁 Структура проекта

```text
vpn-service/
├── bot.py                   # Telegram-бот (Aiogram 3)
├── app.py                   # Веб-сервер / API валидации (FastAPI)
├── index.html               # Веб-интерфейс подписок
├── requirements.txt         # Общий список библиотек (aiogram, fastapi, uvicorn, python-dotenv)
├── .env.example             # Шаблон конфигурации окружения
├── vpn-bot.service          # Служба systemd для Telegram-бота
├── vpn-dashboard.service    # Служба systemd для веб-дашборда
├── nginx-sub.conf           # Конфигурация обратного прокси Nginx
├── install.sh               # Скрипт автоматической установки на сервер
├── update.sh                # Скрипт быстрого обновления кода и служб
├── PROJECT_INSTRUCTIONS.md  # Детальная внутренняя документация логики
├── .gitignore               # Исключение паролей, баз данных и секретов
└── README.md                # Данное руководство
```

---

## 🛠 Управление на сервере

Обе службы работают независимо в `/var/www/vpn-service`:

```bash
# Проверка статуса
systemctl status vpn-bot
systemctl status vpn-dashboard
systemctl status nginx

# Просмотр логов в реальном времени
journalctl -u vpn-bot -f
journalctl -u vpn-dashboard -f

# Перезапуск сервисов
systemctl restart vpn-bot
systemctl restart vpn-dashboard

# Изменение настроек (.env)
nano /var/www/vpn-service/.env
systemctl restart vpn-bot vpn-dashboard
```

### 🔄 Быстрое обновление из Git
```bash
cd /root/vpn-service
sudo bash update.sh
```
Скрипт подтянет свежий коммит, обновит Python-зависимости и перезапустит службы без сброса баз данных и конфигурации.
