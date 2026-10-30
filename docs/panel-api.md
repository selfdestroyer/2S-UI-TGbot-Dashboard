# REST API панели 2S-UI (apiv2): справка для бота

Разобрано по исходникам [shenaba/2s-ui](https://github.com/shenaba/2s-ui), коммит `1196704` (27.10.2026).
Файлы: `api/apiV2Handler.go`, `api/apiService.go`, `service/client.go`, `database/model/model.go`, `web/web.go`.

## 1. Общее устройство

У панели два API на одних и тех же обработчиках:

| | `/<webPath>api/...` (v1) | `/<webPath>apiv2/...` (v2) |
|---|---|---|
| Для кого | веб-интерфейс | интеграции, узлы кластера |
| Авторизация | cookie-сессия после `api/login` + проверка Origin | заголовок `Token: <токен>` |
| Набор действий | всё, включая токены, сертификаты, узлы | клиенты, входящие, статистика, перезапуски |

Боту нужен **v2**. `webPath` по умолчанию `/app/`, порт панели по умолчанию `2095`. Итого базовый адрес:

```
https://<webDomain>:2095/app/apiv2/
```

Маршрутов всего два: `GET apiv2/<действие>` и `POST apiv2/<действие>`.

### Формат ответа

Всегда HTTP 200 (кроме 403 от проверки домена, см. ниже), успех определяется полем `success`:

```json
{ "success": true,  "msg": "",                                  "obj": { ... } }
{ "success": false, "msg": "save: client name \"ivan\" already exists", "obj": null }
```

Проверяйте `success`, а не HTTP-код.

### Подводные камни подключения

1. **Проверка домена.** Если в настройках панели задан `webDomain`, любой запрос (apiv2 тоже), у которого `Host` не совпадает с этим доменом, получает голый **403** без тела. Значит, ходить надо по домену панели, а не по `127.0.0.1`. Если бот на том же сервере и хочется локально — `http://127.0.0.1:2095` с заголовком `Host: <webDomain>` сработает только когда панель слушает без TLS; с TLS нужен ещё SNI, проще ходить по домену.
2. **Токен проверяется в памяти.** Панель грузит токены при старте и после добавления/удаления в интерфейсе. Неверный или истёкший токен: `{"success": false, "msg": "invalid token"}`.
3. **Права токена = права пользователя панели**, к которому он привязан. Отдельных scope нет: токен может всё, что может apiv2, включая `importdb`, `getdb` (скачать всю базу) и `restartApp`. Храните его в `.env` и не логируйте.

### Как получить токен

В панели: **Настройки → Пользователь/API-токены → добавить**. Срок задаётся в днях (`0` = бессрочный). Токен показывается один раз (32 символа).

## 2. Модель клиента (= подписка)

Таблица `clients`, она же JSON в API. Важные для бота поля:

| Поле | Тип | Смысл |
|---|---|---|
| `id` | uint | первичный ключ, присваивается при создании |
| `enable` | bool | включён ли клиент. Панель сама ставит `false`, когда истёк срок или кончился трафик |
| `name` | string | **уникальное** имя, оно же часть ссылки подписки `/sub/<name>` |
| `config` | object | учётные данные по протоколам (UUID, пароли). **Генерирует вызывающий**, см. §4 |
| `inbounds` | uint[] | id входящих, к которым подключён клиент |
| `links` | array | ссылки, генерирует панель (`type`: `local` / `external` / `sub`) |
| `volume` | int64 | лимит трафика в **байтах**, `0` = безлимит |
| `up`, `down` | int64 | израсходовано, байты. Отправка `0` = сброс трафика клиента |
| `expiry` | int64 | окончание, **unix-секунды**, `0` = бессрочно |
| `desc`, `remark`, `group` | string | свободные поля (сейчас бот хранит Telegram ID в `desc`/`remark`) |
| `tgId` | int64 | штатное поле панели под Telegram ID, индексированное, `0` = не привязан |
| `limitIp` | int | лимит одновременных IP, `0` = без лимита |
| `autoReset`, `resetDays`, `resetDayOfMonth`, `delayStart`, `nextReset` | | периодический сброс трафика |
| `createdAt`, `onlineAt` | int64 | ставит сервер, из запроса игнорируются |

> `tgId` — то, что сейчас делается костылём через `desc`. Поле есть в модели и в базе (бот его уже читает как `tg_id`), но в форме панели его нет. Через API его можно записать прямо: `{"id": 5, "tgId": 123456789}`.

## 3. Эндпоинты

### Чтение (GET)

| Запрос | Что вернёт в `obj` |
|---|---|
| `apiv2/clients` | `{clients: [...], clientsSeq}` — список **без** `config` и `links` |
| `apiv2/clients?id=5` | `{clients: [ {...} ]}` — полная строка, с `config` и `links` |
| `apiv2/clients?id=5,7,9` | несколько полных строк |
| `apiv2/inbounds` | все входящие (нужны их `id` для поля `inbounds` клиента) |
| `apiv2/inbounds?id=1` | одно входящее |
| `apiv2/onlines` | кто сейчас онлайн |
| `apiv2/stats?resource=user&tag=<name>&period=hour` | история трафика клиента (`period`: `hour`, `day`, `month`, `60day`, `90day`) |
| `apiv2/status` | состояние сервера и sing-box |
| `apiv2/settings` | настройки панели (тут же `subURI`, домены и т.д.) |
| `apiv2/load` | всё разом, как грузит веб-интерфейс (тяжело, боту не нужно) |

Фильтра «по tgId» на стороне API нет: для поиска подписок пользователя берите `apiv2/clients` и фильтруйте у себя, либо продолжайте читать SQLite (чтение базы в режиме `ro` безопасно, проблема только в записи).

### Запись (POST `apiv2/save`)

Единственный эндпоинт для изменений. Тело — **form-urlencoded** (не JSON!):

| Поле формы | Значение |
|---|---|
| `object` | `clients` |
| `action` | `new` / `edit` / `del` / `addbulk` / `editbulk` / `delbulk` / `resetpolicy` |
| `data` | JSON-строка, зависит от `action` |
| `sync` | `true` — сразу разослать изменения на узлы кластера (без узлов можно не передавать) |

После успешной записи панель сама обновляет пользователей во входящих sing-box, перезапуск не нужен.

Ответ:

```json
{ "success": true, "obj": {
    "object": "clients", "action": "new", "ids": [42],
    "clients": [ { "id": 42, "name": "...", "links": [...], "config": {...}, ... } ]
} }
```

`clients` с полными строками приходит только для одиночных `new`/`edit`. Для `*bulk` и `del` — только `ids`.

#### `new` — создать

`data` — объект клиента. Обязательно: `name` (непустое, уникальное), `config`, `inbounds`. Остальное по умолчанию 0/false/пусто.

```json
{
  "enable": true,
  "name": "tg123456789",
  "config": { "vless": {"name": "tg123456789", "uuid": "…", "flow": "xtls-rprx-vision"}, "...": {} },
  "inbounds": [1, 2],
  "volume": 0,
  "expiry": 1798675200,
  "desc": "123456789",
  "tgId": 123456789
}
```

Ошибки: `client name "…" already exists`, `client name must not be empty`.

#### `edit` — изменить (частичное обновление)

**Ключевая особенность:** `edit` накладывает присланные поля на сохранённую строку. Что не прислали — остаётся как было. Достаточно `id` и изменяемых полей:

```json
{ "id": 42, "expiry": 1806451200, "enable": true }
```

Это и есть «продление». **Обязательно шлите `"enable": true`**, если клиент мог быть уже отключён по сроку: панель выключает истёкших сама и обратно не включает.

Другие полезные варианты:

```json
{ "id": 42, "up": 0, "down": 0 }            // сбросить трафик клиента
{ "id": 42, "enable": false }               // приостановить
{ "id": 42, "tgId": 123456789 }             // привязать Telegram
{ "id": 42, "name": "new-name" }            // переименовать (имя в ссылке подписки сменится!)
```

#### `del` / `delbulk` — удалить

```
data=42            ← для del: просто число
data=[42,43,44]    ← для delbulk: массив
```

#### `addbulk` — создать много

`data` — массив объектов как в `new`. Имена проверяются на уникальность внутри пачки и в базе, всё или ничего.

#### `resetpolicy` — расписание сброса трафика

```json
{ "ids": [42, 43], "autoReset": true, "resetDays": 30, "resetDayOfMonth": 0 }
```

### Прочие POST

| Действие | Что делает |
|---|---|
| `apiv2/resetTraffic` | сбрасывает трафик **всем** клиентам и включает отключённые по трафику |
| `apiv2/restartSb` | перезапуск sing-box |
| `apiv2/restartApp` | перезапуск панели |
| `apiv2/closeSessions` | разорвать сессии |

## 4. Главная ловушка: `config` генерирует клиент API

Сервер **не** создаёт UUID и пароли. Веб-интерфейс делает это в браузере (`frontend/src/types/clients.ts`, функция `randomConfigs`) и присылает готовый `config`. Если создать клиента через API без `config`, он появится, но без учётных данных: ссылки и подключение работать не будут.

Ключи `config` — типы протоколов. Достаточно тех, что реально есть среди ваших входящих, но проще повторить набор панели целиком (так клиент заработает и на входящих, добавленных позже). Поле `name`/`username` внутри каждого протокола панель сама перезапишет именем клиента.

Порт генератора на Python:

```python
import base64, secrets, string, uuid

_ALNUM = string.digits + string.ascii_letters

def _seq(n: int) -> str:
    return "".join(secrets.choice(_ALNUM) for _ in range(n))

def _ss(n: int) -> str:
    return base64.b64encode(secrets.token_bytes(n)).decode()

def random_configs(user: str) -> dict:
    pw, uid = _seq(10), str(uuid.uuid4())
    ss32, ss16 = _ss(32), _ss(16)
    return {
        "mixed":         {"username": user, "password": pw},
        "socks":         {"username": user, "password": pw},
        "http":          {"username": user, "password": pw},
        "shadowsocks":   {"name": user, "password": ss32},
        "shadowsocks16": {"name": user, "password": ss16},
        "shadowtls":     {"name": user, "password": ss32},
        "snell":         {"name": user, "userkey": _seq(32)},
        "vmess":         {"name": user, "uuid": uid, "alterId": 0},
        "vless":         {"name": user, "uuid": uid, "flow": "xtls-rprx-vision"},
        "anytls":        {"name": user, "password": pw},
        "trojan":        {"name": user, "password": pw},
        "naive":         {"username": user, "password": pw},
        "hysteria":      {"name": user, "auth_str": pw},
        "tuic":          {"name": user, "uuid": uid, "password": pw},
        "hysteria2":     {"name": user, "password": pw},
    }
```

> `_ss` повторяет `randomShadowsocksPassword` из `frontend/src/plugins/randomUtil.ts`: base64 от n случайных байт (формат ключей Shadowsocks 2022).

## 5. Как это использует бот

Клиент API живёт в [`panel_api.py`](../panel_api.py): `random_configs()` из §4 и класс `PanelAPI` с методами `get_client`, `create` и `extend`. Сценарий покупки и подтверждения описан в README («Как проходит покупка»).

- `create(tg_id, days)` — `action=new`: случайное имя из 8 символов (до 3 попыток при совпадении имени), `config` по всем протоколам, `inbounds = PANEL_INBOUND_IDS`, `volume = SUB_TRAFFIC_GB`, `limitIp = SUB_DEVICE_LIMIT`, сброс трафика в число `SUB_RESET_DAY_OF_MONTH` или каждые 30 дней, `tgId` = Telegram ID, `desc` = `tg://user?id=<ID> | <ID>`.
- `extend(client_id, days, tg_id)` — `action=edit`: `expiry = max(текущий срок, сейчас) + days`, `enable = true`, `tgId`. Бессрочную подписку (`expiry = 0`) не трогает по сроку, только включает.
- Чтение (поиск подписок пользователя, уведомления, статистика) остаётся на SQLite в режиме `ro`: это быстро и безопасно. Записи — только через API: прямая запись в `s-ui.db` не обновила бы пользователей в sing-box.
