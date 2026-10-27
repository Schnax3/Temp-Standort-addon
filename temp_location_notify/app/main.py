"""Standort-Alarme (temporär) – Home Assistant Add-on.

Lauscht per WebSocket auf state_changed-Events. Regeln (z. B. "Wenn Handy 1
Zone Home betritt, sende Nachricht an Handy 2, 2x") liegen in /data/rules.json
und löschen sich nach Ablauf des Zählers selbst. Es werden keine HA-Automationen
angelegt.
"""
import asyncio
import json
import logging
import os
import time
import uuid

from aiohttp import ClientSession, WSMsgType, web

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("standort-alarme")

TOKEN = os.environ.get("SUPERVISOR_TOKEN", "")
API = "http://supervisor/core/api"
WS_URL = "ws://supervisor/core/websocket"
DATA_FILE = os.environ.get("RULES_FILE", "/data/rules.json")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
PORT = 8099
DONE_KEEP_SECONDS = 24 * 3600  # erledigte Regeln verschwinden nach 24 h
LOG_MAX = 50

NOT_AVAILABLE = (None, "unknown", "unavailable")

TRANSLATIONS = {
    "de": {
        "invalid_input": "Ungültige Eingabe",
        "invalid_event": "Ungültiges Ereignis",
        "count_range": "Anzahl muss zwischen 1 und 99 liegen",
        "invalid_entity": "Ungültige Entität",
        "invalid_notify": "Ungültiger Notify-Dienst",
        "not_found": "Nicht gefunden",
        "new_rule": "Neue Regel",
        "default_title": "Standort-Alarm",
        "default_message": "{person} hat {zone} {event}",
        "rule_created": "Regel „{name}“ erstellt ({count}×)",
        "rule_triggered_remaining": "„{name}“ ausgelöst – noch {remaining}×",
        "rule_finished": "„{name}“ ausgelöst – fertig, Regel läuft ab",
        "send_failed": "Senden fehlgeschlagen ({rule_id}): {error}",
        "auth_failed": "Auth fehlgeschlagen: {data}",
        "websocket_connected": "Mit Home Assistant verbunden",
        "event_error": "Fehler bei Event-Verarbeitung",
        "websocket_lost": "WebSocket getrennt: {error}",
        "zone_home": "Zuhause",
        "event_enter": "betritt",
        "event_leave": "verlässt",
        "event_enter_verb": "betreten",
        "event_leave_verb": "verlassen",
        "notify_invalid": "Ungültiger Notify-Dienst: {notify}",
        "load_error": "rules.json konnte nicht gelesen werden: {error}",
    },
    "en": {
        "invalid_input": "Invalid input",
        "invalid_event": "Invalid event",
        "count_range": "Count must be between 1 and 99",
        "invalid_entity": "Invalid entity",
        "invalid_notify": "Invalid notify service",
        "not_found": "Not found",
        "new_rule": "New rule",
        "default_title": "Location alert",
        "default_message": "{person} has {zone} {event}",
        "rule_created": "Rule “{name}” created ({count}×)",
        "rule_triggered_remaining": "“{name}” triggered – {remaining}× remaining",
        "rule_finished": "“{name}” triggered – done, rule expires",
        "send_failed": "Send failed ({rule_id}): {error}",
        "auth_failed": "Authentication failed: {data}",
        "websocket_connected": "Connected to Home Assistant",
        "event_error": "Error while processing event",
        "websocket_lost": "WebSocket disconnected: {error}",
        "zone_home": "Home",
        "event_enter": "enters",
        "event_leave": "leaves",
        "event_enter_verb": "entered",
        "event_leave_verb": "left",
        "notify_invalid": "Invalid notify service: {notify}",
        "load_error": "rules.json could not be read: {error}",
    },
}


def request_locale(request=None):
    preferred = ""
    if request is not None:
        preferred = request.headers.get("X-Language") or request.headers.get("Accept-Language") or ""
    if not preferred and os.environ.get("LANG"):
        preferred = os.environ.get("LANG", "")
    if preferred:
        lang = preferred.split(",")[0].split(";")[0].strip().lower()
        if lang.startswith("de"):
            return "de"
        if lang.startswith("en"):
            return "en"
    if os.environ.get("LANG", "").lower().startswith("de"):
        return "de"
    return "en"


def t(key, locale="en", **kwargs):
    locale = locale if locale in TRANSLATIONS else "en"
    text = TRANSLATIONS[locale].get(key, TRANSLATIONS["en"].get(key, key))
    try:
        return text.format(**kwargs)
    except (KeyError, ValueError):
        return text


state = {"rules": [], "log": []}
lock = asyncio.Lock()


# ---------------------------------------------------------------- Persistenz
def load():
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        state["rules"] = data.get("rules", [])
        state["log"] = data.get("log", [])
    except FileNotFoundError:
        pass
    except Exception as exc:  # noqa: BLE001
        log.error(t("load_error", locale="en", error=exc))


def save():
    tmp = DATA_FILE + ".tmp"
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    os.replace(tmp, DATA_FILE)


def add_log(text):
    state["log"].insert(0, {"ts": time.time(), "text": text})
    del state["log"][LOG_MAX:]


# ------------------------------------------------------------- HA REST-Zugriff
def headers():
    return {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


async def ha_get(session, path):
    async with session.get(f"{API}{path}", headers=headers()) as resp:
        resp.raise_for_status()
        return await resp.json()


async def ha_post(session, path, payload):
    async with session.post(f"{API}{path}", headers=headers(), json=payload) as resp:
        if resp.status >= 400:
            body = await resp.text()
            raise RuntimeError(f"HTTP {resp.status}: {body[:200]}")
        return await resp.text()


def friendly(st):
    attrs = (st or {}).get("attributes", {})
    return attrs.get("friendly_name") or (st or {}).get("entity_id", "?")


async async def zone_state_name(session, zone_entity):
    """Wert, den ein Tracker/Person-State annimmt, wenn er in der Zone ist."""
    if zone_entity == "zone.home":
        return "home", "Home"
    try:
        st = await ha_get(session, f"/states/{zone_entity}")
        name = st.get("attributes", {}).get("friendly_name") or zone_entity.split(".", 1)[1]
        return name, name
    except Exception:  # noqa: BLE001
        fallback = zone_entity.split(".", 1)[1]
        return fallback, fallback


# ------------------------------------------------------------ Benachrichtigung
def render(template, person, zone, event, locale="en"):
    verb = t("event_enter_verb" if event == "enter" else "event_leave_verb", locale=locale)
    return (
        template.replace("{person}", person)
        .replace("{zone}", zone)
        .replace("{event}", verb)
    )


async def send_notification(session, rule, person, zone_label):
    locale = rule.get("locale", "en")
    domain, _, service = rule["notify"].partition(".")
    if domain != "notify" or not service:
        raise RuntimeError(t("notify_invalid", locale=locale, notify=rule["notify"]))
    payload = {
        "title": render(rule.get("title") or t("default_title", locale=locale), person, zone_label, rule["event"], locale=locale),
        "message": render(rule["message"], person, zone_label, rule["event"], locale=locale),
    }
    await ha_post(session, f"/services/notify/{service}", payload)


async def fire_rule(session, rule, person, zone_label):
    locale = rule.get("locale", "en")
    async with lock:
        if rule["remaining"] <= 0 or not rule.get("enabled", True):
            return
        try:
            await send_notification(session, rule, person, zone_label)
        except Exception as exc:  # noqa: BLE001
            log.error(t("send_failed", locale=locale, rule_id=rule["id"], error=exc))
            add_log(f"Fehler bei „{rule['name']}“: {exc}")
            save()
            return
        rule["remaining"] -= 1
        rule["last_triggered"] = time.time()
        if rule["remaining"] <= 0:
            rule["finished"] = time.time()
            add_log(t("rule_finished", locale=locale, name=rule["name"]))
        else:
            add_log(t("rule_triggered_remaining", locale=locale, name=rule["name"], remaining=rule["remaining"]))
        save()


async def on_state_changed(session, data):
    entity_id = data.get("entity_id")
    if not any(r["entity"] == entity_id for r in state["rules"]):
        return
    old = data.get("old_state")
    new = data.get("new_state")
    if not new:
        return
    old_s = old.get("state") if old else None
    new_s = new.get("state")
    if old_s in NOT_AVAILABLE or new_s in NOT_AVAILABLE or old_s == new_s:
        return
    person = friendly(new)
    for rule in list(state["rules"]):
        if rule["entity"] != entity_id or rule["remaining"] <= 0 or not rule.get("enabled", True):
            continue
        target, zone_label = await zone_state_name(session, rule["zone"])
        if rule["event"] == "enter" and new_s == target and old_s != target:
            await fire_rule(session, rule, person, zone_label)
        elif rule["event"] == "leave" and old_s == target and new_s != target:
            await fire_rule(session, rule, person, zone_label)


async def ws_loop(app):
    session = app["session"]
    while True:
        try:
            async with session.ws_connect(WS_URL, heartbeat=30) as ws:
                await ws.receive_json()  # auth_required
                await ws.send_json({"type": "auth", "access_token": TOKEN})
                msg = await ws.receive_json()
                if msg.get("type") != "auth_ok":
                    raise RuntimeError(t("auth_failed", locale="en", data=msg))
                await ws.send_json({"id": 1, "type": "subscribe_events", "event_type": "state_changed"})
                log.info(t("websocket_connected", locale="en"))
                async for m in ws:
                    if m.type == WSMsgType.TEXT:
                        d = m.json()
                        if d.get("type") == "event":
                            try:
                                await on_state_changed(session, d["event"]["data"])
                            except Exception:  # noqa: BLE001
                                log.exception(t("event_error", locale="en"))
                    elif m.type in (WSMsgType.CLOSED, WSMsgType.ERROR):
                        break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning(t("websocket_lost", locale="en", error=exc))
        await asyncio.sleep(5)


async def purge_loop():
    while True:
        await asyncio.sleep(600)
        async with lock:
            now = time.time()
            before = len(state["rules"])
            state["rules"] = [
                r for r in state["rules"]
                if not (r["remaining"] <= 0 and now - r.get("finished", now) > DONE_KEEP_SECONDS)
            ]
            if len(state["rules"]) != before:
                save()


# ------------------------------------------------------------------- HTTP-API
async def index(_request):
    return web.FileResponse(os.path.join(STATIC_DIR, "index.html"))


async def api_options(request):
    session = request.app["session"]
    locale = request_locale(request)
    states = await ha_get(session, "/states")
    services = await ha_get(session, "/services")
    entities = [
        {"id": s["entity_id"], "name": friendly(s)}
        for s in states
        if s["entity_id"].startswith(("person.", "device_tracker."))
    ]
    entities.sort(key=lambda e: (not e["id"].startswith("person."), e["name"].lower()))
    zones = [
        {"id": s["entity_id"], "name": t("zone_home", locale=locale) if s["entity_id"] == "zone.home" else friendly(s)}
        for s in states
        if s["entity_id"].startswith("zone.")
    ]
    zones.sort(key=lambda z: (z["id"] != "zone.home", z["name"].lower()))
    notify = []
    for dom in services:
        if dom.get("domain") == "notify":
            for name in dom.get("services", {}):
                if name.startswith("mobile_app_"):
                    label = name[len("mobile_app_"):].replace("_", " ")
                else:
                    label = name.replace("_", " ")
                notify.append({"id": f"notify.{name}", "name": label, "mobile": name.startswith("mobile_app_")})
    notify.sort(key=lambda n: (not n["mobile"], n["name"].lower()))
    return web.json_response({"entities": entities, "zones": zones, "notify": notify})


def public_state():
    return {"rules": state["rules"], "log": state["log"], "now": time.time()}


async def api_rules(_request):
    return web.json_response(public_state())


async def api_create(request):
    locale = request_locale(request)
    try:
        body = await request.json()
        entity = str(body["entity"])
        zone = str(body["zone"])
        notify = str(body["notify"])
        event = body.get("event", "enter")
        count = int(body.get("count", 1))
        message = str(body.get("message", "")).strip()
    except (KeyError, ValueError, TypeError):
        return web.json_response({"error": t("invalid_input", locale=locale)}, status=400)
    if event not in ("enter", "leave"):
        return web.json_response({"error": t("invalid_event", locale=locale)}, status=400)
    if not 1 <= count <= 99:
        return web.json_response({"error": t("count_range", locale=locale)}, status=400)
    if not entity.startswith(("person.", "device_tracker.")) or not zone.startswith("zone."):
        return web.json_response({"error": t("invalid_entity", locale=locale)}, status=400)
    if not notify.startswith("notify."):
        return web.json_response({"error": t("invalid_notify", locale=locale)}, status=400)
    if not message:
        message = t("default_message", locale=locale, person="{person}", zone="{zone}", event="{event}")
    name = str(body.get("name", "")).strip() or None
    async with lock:
        rule = {
            "id": uuid.uuid4().hex[:8],
            "name": name or t("new_rule", locale=locale),
            "entity": entity,
            "entity_name": str(body.get("entity_name", entity)),
            "zone": zone,
            "zone_name": str(body.get("zone_name", zone)),
            "event": event,
            "notify": notify,
            "notify_name": str(body.get("notify_name", notify)),
            "title": str(body.get("title", "")).strip() or t("default_title", locale=locale),
            "message": message,
            "total": count,
            "remaining": count,
            "enabled": True,
            "created": time.time(),
            "last_triggered": None,
            "locale": locale,
        }
        if not name:
            verb = t("event_enter" if event == "enter" else "event_leave", locale=locale)
            rule["name"] = f"{rule['entity_name']} {verb} {rule['zone_name']}"
        state["rules"].append(rule)
        add_log(t("rule_created", locale=locale, name=rule["name"], count=count))
        save()
    return web.json_response(public_state())


async def api_toggle(request):
    rid = request.match_info["rid"]
    async with lock:
        for r in state["rules"]:
            if r["id"] == rid:
                r["enabled"] = not r.get("enabled", True)
                save()
                break
    return web.json_response(public_state())


async def api_delete(request):
    rid = request.match_info["rid"]
    async with lock:
        state["rules"] = [r for r in state["rules"] if r["id"] != rid]
        save()
    return web.json_response(public_state())


async def api_clear_done(_request):
    async with lock:
        state["rules"] = [r for r in state["rules"] if r["remaining"] > 0]
        save()
    return web.json_response(public_state())


async def api_test(request):
    rid = request.match_info["rid"]
    locale = request_locale(request)
    rule = next((r for r in state["rules"] if r["id"] == rid), None)
    if not rule:
        return web.json_response({"error": t("not_found", locale=locale)}, status=404)
    try:
        await send_notification(request.app["session"], rule, rule["entity_name"], rule["zone_name"])
    except Exception as exc:  # noqa: BLE001
        return web.json_response({"error": str(exc)}, status=502)
    return web.json_response({"ok": True})


# --------------------------------------------------------------------- Start
async def on_startup(app):
    app["session"] = ClientSession()
    app["tasks"] = [
        asyncio.create_task(ws_loop(app)),
        asyncio.create_task(purge_loop()),
    ]


async def on_cleanup(app):
    for t in app["tasks"]:
        t.cancel()
    await app["session"].close()


def make_app():
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/api/options", api_options)
    app.router.add_get("/api/rules", api_rules)
    app.router.add_post("/api/rules", api_create)
    app.router.add_post("/api/rules/{rid}/toggle", api_toggle)
    app.router.add_post("/api/rules/{rid}/test", api_test)
    app.router.add_delete("/api/rules/{rid}", api_delete)
    app.router.add_post("/api/clear-done", api_clear_done)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    load()
    web.run_app(make_app(), host="0.0.0.0", port=PORT, print=None)
