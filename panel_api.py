"""Клиент REST API панели 2S-UI (apiv2) для выдачи и продления подписок.

Справка по API: docs/panel-api.md.
"""
import base64
import json
import secrets
import string
import time
import uuid

import aiohttp

_ALNUM = string.digits + string.ascii_letters
DAY = 86400


class PanelAPIError(Exception):
    pass


def _seq(n: int) -> str:
    return "".join(secrets.choice(_ALNUM) for _ in range(n))


def _ss(n: int) -> str:
    # Как randomShadowsocksPassword в панели: base64 от n случайных байт
    return base64.b64encode(secrets.token_bytes(n)).decode()


def random_configs(user: str) -> dict:
    """Учётные данные по всем протоколам, как их генерирует веб-интерфейс панели.

    Сервер панели их не создаёт: без config клиент появится, но подключиться не сможет.
    """
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


def client_desc(tg_id: int) -> str:
    """Описание клиента для админа: ссылка на профиль в Telegram и ID.

    Ссылка по ID, а не по юзернейму: юзернейм пользователь может сменить, ID — нет.
    """
    return f"tg://user?id={tg_id} | {tg_id}"


def _desc_is_ours(desc: str, tg_id: int) -> bool:
    """Описание пустое, только ID или уже сгенерировано ботом (в т.ч. старое со ссылкой t.me) — его можно обновить.

    Свою заметку админа бот не перезаписывает.
    """
    desc = desc.strip()
    return (not desc or desc == str(tg_id)
            or (desc.startswith(("https://t.me/", "tg://user?id=")) and desc.endswith(f"| {tg_id}")))


def expiry_seconds(expiry: int | None) -> int:
    """Срок панели в секундах (на всякий случай понимает и миллисекунды)."""
    expiry = int(expiry or 0)
    return expiry // 1000 if expiry > 100_000_000_000 else expiry


class PanelAPI:
    def __init__(self, base_url: str, token: str, inbound_ids: list[int],
                 traffic_gb: int = 0, device_limit: int = 0, reset_day_of_month: int = 0, group: str = ""):
        # base_url вида "https://panel.example.com:2095/app/apiv2/"
        self.base = base_url.rstrip("/") + "/"
        self.headers = {"Token": token}
        self.inbound_ids = inbound_ids
        self.traffic_gb = traffic_gb
        self.device_limit = device_limit
        # Число месяца для сброса трафика; 0 — сброс каждые 30 дней от создания
        self.reset_day_of_month = reset_day_of_month
        # Группа клиента в панели для купленных подписок; пусто — не задавать
        self.group = group

    async def _call(self, method: str, action: str, *, params=None, form=None):
        try:
            async with aiohttp.ClientSession(headers=self.headers) as s:
                async with s.request(method, self.base + action, params=params, data=form,
                                     timeout=aiohttp.ClientTimeout(total=20)) as r:
                    if r.status == 403:
                        raise PanelAPIError("403: адрес PANEL_API_URL не совпадает с доменом панели (webDomain)")
                    if r.status != 200:
                        raise PanelAPIError(f"HTTP {r.status} от панели")
                    body = await r.json(content_type=None)
        except aiohttp.ClientError as e:
            raise PanelAPIError(f"панель недоступна: {e}") from e
        except TimeoutError as e:
            raise PanelAPIError("панель не ответила за 20 секунд") from e
        except ValueError as e:
            raise PanelAPIError("панель вернула не JSON (проверьте PANEL_API_URL)") from e
        if not isinstance(body, dict) or not body.get("success"):
            msg = body.get("msg") if isinstance(body, dict) else None
            raise PanelAPIError(msg or "панель отклонила запрос")
        return body.get("obj")

    async def _save(self, action: str, data) -> dict:
        return await self._call("POST", "save", form={
            "object": "clients", "action": action, "data": json.dumps(data),
        })

    async def get_client(self, client_id: int) -> dict | None:
        obj = await self._call("GET", "clients", params={"id": str(client_id)})
        rows = (obj or {}).get("clients") or []
        return rows[0] if rows else None

    async def create(self, tg_id: int, days: int) -> dict:
        """Создаёт клиента со случайным именем на `days` дней и возвращает его строку."""
        last_error = None
        for _ in range(3):
            name = _seq(8)
            try:
                obj = await self._save("new", {
                    "enable": True,
                    "name": name,
                    "config": random_configs(name),
                    "inbounds": self.inbound_ids,
                    "expiry": int(time.time()) + days * DAY,
                    "volume": self.traffic_gb * 1024 ** 3,
                    "limitIp": self.device_limit,
                    **self._reset_schedule(),
                    "desc": client_desc(tg_id),
                    "tgId": tg_id,
                    "group": self.group,
                })
            except PanelAPIError as e:
                if "already exists" in str(e):
                    last_error = e
                    continue
                raise
            rows = obj.get("clients") or []
            if not rows:
                raise PanelAPIError("панель не вернула созданного клиента")
            return rows[0]
        raise last_error

    def _reset_schedule(self) -> dict:
        """Лимит трафика месячный: сброс в заданное число месяца или каждые 30 дней."""
        if self.traffic_gb <= 0:
            return {"autoReset": False, "resetDays": 0, "resetDayOfMonth": 0}
        if self.reset_day_of_month > 0:
            return {"autoReset": True, "resetDays": 0, "resetDayOfMonth": self.reset_day_of_month}
        return {"autoReset": True, "resetDays": 30, "resetDayOfMonth": 0}

    async def extend(self, client_id: int, days: int, tg_id: int) -> dict:
        """Продлевает клиента на `days` дней от текущего срока (или от сейчас, если истёк) и включает его."""
        client = await self.get_client(client_id)
        if not client:
            raise PanelAPIError(f"клиент #{client_id} не найден в панели")
        now = int(time.time())
        current = expiry_seconds(client.get("expiry"))
        # expiry 0 означает «бессрочно»: такую подписку продлевать некуда, срок не трогаем
        data = {"id": client_id, "enable": True, "tgId": tg_id}
        # Пустое или старое описание приводим к единому виду со ссылкой
        if _desc_is_ours(client.get("desc") or "", tg_id):
            data["desc"] = client_desc(tg_id)
        # Группу ставим только клиенту без группы: свою (friends, family…) админ задал сам
        if self.group and not (client.get("group") or "").strip():
            data["group"] = self.group
        if current > 0:
            data["expiry"] = max(current, now) + days * DAY
        obj = await self._save("edit", data)
        rows = obj.get("clients") or []
        return rows[0] if rows else {**client, **data}
