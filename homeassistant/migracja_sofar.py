# -*- coding: utf-8 -*-
"""Migracja HA z SolaX Modbus (sofar_modbus_*) na Solarman (sofar_logger_*) + watchdog.

Dlaczego: obie integracje łączyły się z tym samym loggerem 192.168.50.15:8899
i dostawały nawzajem swoje odpowiedzi; 1-2.10.2026 SolaX leżał przez to 21 h.

Uruchamiać na hoście SSH (Advanced SSH & Web Terminal), skrypt przez stdin:

    ssh ha 'python3 - '              < homeassistant/migracja_sofar.py   # na sucho
    ssh ha 'python3 - --apply'       < homeassistant/migracja_sofar.py
    ssh ha 'python3 - --disable-solax' < homeassistant/migracja_sofar.py
    ssh ha 'python3 - --rollback /config/backup_sofar_migracja/<znacznik>' < ...

Na sucho: pokazuje, co zmieni, i renderuje nowe szablony w HA (bez zapisu).
--apply: robi kopię do /config/backup_sofar_migracja/<znacznik>/, potem zmienia:
  1. automatyzację „EV - Można ładować auto z nadwyżek PV” (SOC, PV w W),
  2. trzy helpery-szablony (Status magazynu, Status sieci, Realny koszt kWh),
  3. dashboard (3 wiersze encji),
  4. recorder include w configuration.yaml (działa dopiero po restarcie HA),
  5. dodaje automatyzację-watchdog `sofar_watchdog`.
Wpisu SolaX nie rusza - osobno --disable-solax, po wdrożeniu ev_charger.py.
--rollback: przywraca wszystko z kopii, usuwa watchdog i włącza SolaX.

Tylko biblioteka standardowa (na hoście SSH nie ma websocket-client).
"""
import base64
import json
import os
import socket
import struct
import sys
import time
import urllib.error
import urllib.request

TOKEN = os.environ["SUPERVISOR_TOKEN"]
BACKUP_ROOT = "/config/backup_sofar_migracja"
CONFIG_YAML = "/config/configuration.yaml"

SOLARMAN_ENTRY = "01K4YP31SZ7CWX7GMKGT0DFSQE"
SOLAX_ENTRY = "01K5926B3FBZQQ4JYQ969CM05A"
AUTOMATION_PV = "1777386014547"
WATCHDOG_ID = "sofar_watchdog"
TEMPLATE_ENTRIES = (
    "01KQAAZTBV2R1JGTW0PVN6BD1S",   # Status magazynu energii
    "01KQAD27RV9WDSZE2J7XECXRNF",   # Status sieci
    "01KQADG0PKT93PZFK34FTCS25M",   # Realny koszt kWh dziś
)

# Podmiany w szablonach. Moc: SolaX w kW, Solarman w W - dzielimy przez 1000,
# żeby progi (0.1 kW) i teksty („... kW”) zostały bez zmian. Znak ten sam
# (rejestry 0x0488 i 0x0606 czytane jako signed, bez odwracania).
TEMPLATE_REPLACE = [
    ("states('sensor.sofar_modbus_inverter_battery_power_total') | float",
     "states('sensor.sofar_logger_battery_power') | float / 1000"),
    ("states('sensor.sofar_modbus_inverter_active_power_pcc_total') | float",
     "states('sensor.sofar_logger_activepower_pcc_total') | float / 1000"),
    ("sensor.sofar_modbus_inverter_load_consumption_today",
     "sensor.sofar_logger_today_load_consumption"),
]

# Dashboard: zwykłe wiersze encji, jednostkę pokazuje sama encja.
LOVELACE_REPLACE = [
    ("sensor.sofar_modbus_battery_1_1_soc", "sensor.sofar_logger_battery"),
    ("sensor.sofar_modbus_inverter_pv_power_total", "sensor.sofar_logger_pv_power"),
    ("sensor.sofar_modbus_inverter_active_power_load_sys", "sensor.sofar_logger_activepower_load_sys"),
]

RECORDER_OLD = "      - sensor.sofar_modbus_battery_1_1_soc\n"
RECORDER_NEW = "      - sensor.sofar_logger_battery\n"

AUTOMATION_PV_NEW = {
    "id": AUTOMATION_PV,
    "alias": "EV - Można ładować auto z nadwyżek PV",
    "description": "Powiadomienie gdy bateria ≥90% i PV produkuje ponad 2kW",
    "triggers": [{"trigger": "numeric_state", "entity_id": "sensor.sofar_logger_battery",
                  "above": 90, "for": {"minutes": 5}}],
    "conditions": [{"condition": "numeric_state", "entity_id": "sensor.sofar_logger_pv_power",
                    "above": 2000}],
    "actions": [{"action": "notify.mobile_app_tomek_oneplus_12", "data": {
        "title": "☀️ Możesz ładować auto!",
        "message": "Bateria: {{ states('sensor.sofar_logger_battery') }}% PV: "
                   "{{ states('sensor.sofar_logger_pv_power') | int(0) }} W "
                   "Podłącz samochód - są nadwyżki ze słońca!\n"}}],
    "mode": "single",
}

# Brak danych = SOC niedostępny ALBO licznik cyklu Solarmana stoi > 2 min
# (sensor.sofar_logger_update_interval zmienia wartość przy każdym odczycie,
# co ~6 s). Warunek musi trwać 10 min, więc restart HA go nie wyzwala.
WATCHDOG_STALE = (
    "{% set soc = states('sensor.sofar_logger_battery') %}"
    "{% set iv = states.sensor.sofar_logger_update_interval %}"
    "{{ soc in ['unavailable', 'unknown'] or iv is none"
    " or (now() - iv.last_updated).total_seconds() > 120 }}"
)
WATCHDOG_OK = (
    "{% set iv = states.sensor.sofar_logger_update_interval %}"
    "{{ states('sensor.sofar_logger_battery') not in ['unavailable', 'unknown']"
    " and iv is not none and (now() - iv.last_updated).total_seconds() < 60 }}"
)
WATCHDOG = {
    "id": WATCHDOG_ID,
    "alias": "Sofar - watchdog integracji Solarman",
    "description": "Brak danych z falownika przez 10 min -> przeładuj wpis Solarman i "
                   "powiadom. Bez danych ev_charger widzi SOC=0 i nie ładuje z PV. "
                   "Najwyżej raz na 30 min.",
    "triggers": [{"trigger": "template", "value_template": WATCHDOG_STALE,
                  "for": {"minutes": 10}}],
    "conditions": [{"condition": "template", "value_template":
        "{{ this.attributes.last_triggered is none or "
        "(now() - this.attributes.last_triggered).total_seconds() > 1800 }}"}],
    "actions": [
        {"action": "notify.notify", "data": {
            "title": "Sofar: brak danych",
            "message": "Integracja Solarman nie daje danych od 10 min. Przeładowuję ją."}},
        {"action": "homeassistant.reload_config_entry", "data": {"entry_id": SOLARMAN_ENTRY}},
        {"wait_template": WATCHDOG_OK, "timeout": "00:05:00", "continue_on_timeout": True},
        {"if": [{"condition": "template", "value_template": "{{ wait.completed }}"}],
         "then": [{"action": "notify.notify", "data": {
             "title": "Sofar: dane wróciły",
             "message": "Po przeładowaniu integracji Solarman dane z falownika płyną."}}],
         "else": [{"action": "notify.notify", "data": {
             "title": "Sofar: przeładowanie nie pomogło",
             "message": "Brak danych z falownika mimo przeładowania. Ładowarka nie "
                        "ładuje z PV. Sprawdź logger 192.168.50.15 i log HA."}}]},
    ],
    "mode": "single",
}


# ── REST ─────────────────────────────────────────────────────────────────────
def rest(method, path, body=None):
    req = urllib.request.Request(
        "http://supervisor/core/api/" + path, method=method,
        data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + TOKEN, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as f:
        raw = f.read()
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return raw.decode()


# ── WebSocket (RFC 6455, minimalny klient) ───────────────────────────────────
class WS:
    def __init__(self):
        self.sock = socket.create_connection(("supervisor", 80), timeout=60)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((
            "GET /core/websocket HTTP/1.1\r\nHost: supervisor\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Key: " + key + "\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += self.sock.recv(1)
        self.buf, self.msg_id = b"", 0
        self._recv()
        self._send({"type": "auth", "access_token": TOKEN})
        assert self._recv()["type"] == "auth_ok"

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise EOFError("WebSocket zamknięty")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _recv(self):
        payload = b""
        while True:
            h = self._read(2)
            n = h[1] & 0x7f
            if n == 126:
                n = struct.unpack(">H", self._read(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._read(8))[0]
            payload += self._read(n)
            if h[0] & 0x80:
                return json.loads(payload)

    def _send(self, obj):
        p = json.dumps(obj).encode()
        mask = os.urandom(4)
        n = len(p)
        if n < 126:
            head = bytes([0x81, 0x80 | n])
        elif n < 65536:
            head = bytes([0x81, 0x80 | 126]) + struct.pack(">H", n)
        else:
            head = bytes([0x81, 0x80 | 127]) + struct.pack(">Q", n)
        self.sock.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(p)))

    def call(self, obj):
        self.msg_id += 1
        obj["id"] = self.msg_id
        self._send(obj)
        while True:
            r = self._recv()
            if r.get("id") == self.msg_id:
                if not r.get("success", True):
                    raise RuntimeError(f"{obj['type']}: {r.get('error')}")
                return r.get("result")


# ── Odczyt stanu bieżącego ───────────────────────────────────────────────────
def read_current(ws):
    entries = {e["entry_id"]: e for e in
               json.load(open("/config/.storage/core.config_entries"))["data"]["entries"]}
    try:
        watchdog = rest("GET", f"config/automation/config/{WATCHDOG_ID}")
    except urllib.error.HTTPError:
        watchdog = None
    return {
        "automation_pv": rest("GET", f"config/automation/config/{AUTOMATION_PV}"),
        "watchdog": watchdog,
        "templates": {eid: entries[eid]["options"] for eid in TEMPLATE_ENTRIES},
        "lovelace": ws.call({"type": "lovelace/config", "url_path": None}),
        "configuration_yaml": open(CONFIG_YAML, encoding="utf-8").read(),
    }


def new_template_options(opts):
    state = opts["state"]
    for old, new in TEMPLATE_REPLACE:
        state = state.replace(old, new)
    return dict(opts, state=state)


def new_lovelace(cfg):
    s = json.dumps(cfg, ensure_ascii=False)
    for old, new in LOVELACE_REPLACE:
        s = s.replace(f'"{old}"', f'"{new}"')
    return json.loads(s)


def set_template_options(entry_id, opts):
    """Zmiana helpera przez jego options flow - tak samo jak edycja w UI."""
    flow = rest("POST", "config/config_entries/options/flow", {"handler": entry_id})
    fields = {f["name"] for f in flow.get("data_schema", [])}
    user_input = {k: v for k, v in opts.items() if k in fields}
    res = rest("POST", f"config/config_entries/options/flow/{flow['flow_id']}", user_input)
    if res.get("type") != "create_entry":
        raise RuntimeError(f"helper {entry_id}: {res}")


def render(tpl):
    return str(rest("POST", "template", {"template": tpl}))


# ── Tryby ────────────────────────────────────────────────────────────────────
def plan(cur):
    """Wylicza stan docelowy i sprawdza go na sucho. Zwraca listę problemów."""
    problems = []
    print("== Automatyzacja PV:", cur["automation_pv"]["alias"])
    print("   trigger:", cur["automation_pv"]["triggers"][0]["entity_id"], "->",
          AUTOMATION_PV_NEW["triggers"][0]["entity_id"])
    print("   warunek PV: >", cur["automation_pv"]["conditions"][0]["above"], "(kW) ->",
          AUTOMATION_PV_NEW["conditions"][0]["above"], "(W)")
    print("   treść:", render(AUTOMATION_PV_NEW["actions"][0]["data"]["message"]).strip())

    print("== Helpery-szablony:")
    for eid, opts in cur["templates"].items():
        new = new_template_options(opts)
        if "sofar_modbus" in new["state"]:
            problems.append(f"helper {opts['name']}: zostało sofar_modbus")
        old_val = render(opts["state"]).strip()
        new_val = render(new["state"]).strip()
        print(f"   {opts['name']}: teraz «{old_val}» -> po zmianie «{new_val}»")

    print("== Dashboard:")
    new_ll = new_lovelace(cur["lovelace"])
    left = json.dumps(new_ll).count("sofar_modbus")
    print(f"   podmian: {sum(json.dumps(cur['lovelace']).count(o) for o, _ in LOVELACE_REPLACE)},"
          f" sofar_modbus po zmianie: {left}")
    if left:
        problems.append("dashboard: zostało sofar_modbus")

    print("== Recorder (configuration.yaml):")
    if RECORDER_OLD in cur["configuration_yaml"]:
        print("   sofar_modbus_battery_1_1_soc -> sofar_logger_battery (działa po restarcie HA)")
    elif RECORDER_NEW in cur["configuration_yaml"]:
        print("   już zmienione")
    else:
        problems.append("recorder: nie znalazłem linii do podmiany")

    print("== Watchdog:", "już istnieje (zostanie nadpisany)" if cur["watchdog"] else "nowy")
    print("   warunek „brak danych” teraz:", render(WATCHDOG_STALE).strip(),
          "| „dane OK” teraz:", render(WATCHDOG_OK).strip())
    if render(WATCHDOG_STALE).strip() != "False" or render(WATCHDOG_OK).strip() != "True":
        problems.append("watchdog: przy działającym Solarmanie warunki powinny dać False/True")
    return problems


def apply(ws, cur):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bdir = os.path.join(BACKUP_ROOT, stamp)
    os.makedirs(bdir)
    with open(os.path.join(bdir, "stan.json"), "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in cur.items() if k != "configuration_yaml"}, f,
                  ensure_ascii=False, indent=1)
    with open(os.path.join(bdir, "configuration.yaml"), "w", encoding="utf-8") as f:
        f.write(cur["configuration_yaml"])
    print("Kopia:", bdir)

    rest("POST", f"config/automation/config/{AUTOMATION_PV}", AUTOMATION_PV_NEW)
    print("OK automatyzacja PV")
    for eid, opts in cur["templates"].items():
        set_template_options(eid, new_template_options(opts))
        print("OK helper", opts["name"])
    ws.call({"type": "lovelace/config/save", "url_path": None,
             "config": new_lovelace(cur["lovelace"])})
    print("OK dashboard")
    if RECORDER_OLD in cur["configuration_yaml"]:
        with open(CONFIG_YAML, "w", encoding="utf-8") as f:
            f.write(cur["configuration_yaml"].replace(RECORDER_OLD, RECORDER_NEW))
        chk = rest("POST", "config/core/check_config")
        print("OK recorder, check_config:", chk.get("result"), chk.get("errors") or "")
    rest("POST", f"config/automation/config/{WATCHDOG_ID}", WATCHDOG)
    print("OK watchdog")
    print(f"\nWycofanie: ssh ha 'python3 - --rollback {bdir}' < homeassistant/migracja_sofar.py")


def rollback(ws, bdir):
    stan = json.load(open(os.path.join(bdir, "stan.json"), encoding="utf-8"))
    rest("POST", f"config/automation/config/{AUTOMATION_PV}", stan["automation_pv"])
    print("OK automatyzacja PV przywrócona")
    for eid, opts in stan["templates"].items():
        set_template_options(eid, opts)
        print("OK helper", opts["name"])
    ws.call({"type": "lovelace/config/save", "url_path": None, "config": stan["lovelace"]})
    print("OK dashboard")
    with open(os.path.join(bdir, "configuration.yaml"), encoding="utf-8") as f:
        old_yaml = f.read()
    with open(CONFIG_YAML, "w", encoding="utf-8") as f:
        f.write(old_yaml)
    print("OK configuration.yaml (recorder po restarcie HA)")
    if stan.get("watchdog"):
        rest("POST", f"config/automation/config/{WATCHDOG_ID}", stan["watchdog"])
    else:
        try:
            rest("DELETE", f"config/automation/config/{WATCHDOG_ID}")
        except urllib.error.HTTPError:
            pass
    print("OK watchdog")
    ws.call({"type": "config_entries/disable", "entry_id": SOLAX_ENTRY, "disabled_by": None})
    print("OK SolaX włączony")


def main():
    args = sys.argv[1:]
    ws = WS()
    if args[:1] == ["--rollback"]:
        rollback(ws, args[1])
        return
    if args[:1] == ["--disable-solax"]:
        ws.call({"type": "config_entries/disable", "entry_id": SOLAX_ENTRY, "disabled_by": "user"})
        print("OK SolaX wyłączony (wpis zostaje, włączenie: --rollback albo UI)")
        return
    cur = read_current(ws)
    problems = plan(cur)
    if problems:
        print("\nPROBLEMY:\n  " + "\n  ".join(problems))
        sys.exit(1)
    if args[:1] == ["--apply"]:
        print()
        apply(ws, cur)
    else:
        print("\nNa sucho - nic nie zmieniono. Zmiany: --apply")


if __name__ == "__main__":
    main()
