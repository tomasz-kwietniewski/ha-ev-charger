# Kontekst projektu: Inteligentne ładowanie EV z nadwyżek PV
## Plik eksportowy dla Claude Code (Windsurf)
*Wygenerowany: 2026-05-05*

---

## 1. PROFIL UŻYTKOWNIKA

**Tomasz Kwietniewski** — tomaszkwietniewski.pl
Dom drewniany szkieletowy 125 m², Muszuły k. Konotopy, Mazowsze
Dom w pełni elektryczny (bez gazu), moc przyłączeniowa 11 kW

---

## 2. INFRASTRUKTURA TECHNICZNA

### Fotowoltaika
- **9 kWp** — 18 paneli JA Solar JAM60D40 500W (n-type)
- 2 stringi (9+9 paneli)
- Falownik hybrydowy: **Sofar HYD 8KTL-3PH**
- Planowany uzysk: ~9000 kWh/rok

### Magazyn energii
- **Sofar BTS E15-DS5** — 15 kWh (3 moduły BTS 5K)
- Napięcie nominalne: 400V DC
- Komunikacja z falownikiem: CAN

### EPS / Backup
- **Encor SwitchBox** (Corab) — przełącznik sieciowy/awaryjny
- Kompatybilny z Sofar HYD seria 3PH

### Ładowarka EV
- **dé EV Charger 11kW WiFi Typ 2** — przenośna wallbox
- Protokół: **Tuya 3.5** (WAŻNE: Local Tuya nie obsługuje 3.5, tylko 3.4!)
- IP lokalne: `192.168.50.250` (stały adres w routerze)
- Device ID: `bffc3e022bd8ef987fhias`
- Local Key: `h|p4Nv.-V/U7@Oa3`
- Serwery Tuya dla Polski: **Frankfurt AWS** (nie Chiny)
- 3-fazowa, max 16A = ~11kW

### Samochód
- **Citroën Spacetourer Electric** 75 kWh, rok 2023
- VIN: VF7VZZKXZPZ079253
- Wbudowana ładowarka: 3-fazowa

### Ogrzewanie / Chłodzenie
- Pompy ciepła powietrze-powietrze Fujitsu:
  - AOYG12KGCB (12 kBTU)
  - AOEG24KBTB (24 kBTU)
  - ASYG12KGTF (jednostka wewnętrzna)
  - ARXG24KMLA (jednostka wewnętrzna)

### Pozostałe
- Rekuperator: VENTS VUT 300 V2 mini EC
- CWU: Bojler Ariston VELIS EVO 100
- Kosiarka: Cramer 48V 48LM48SPK4 + 2 akumulatory

---

## 3. HOME ASSISTANT

### Platforma
- HA na Synology NAS DS420+
- Folder konfiguracji: `/config` (alias `/homeassistant`)
- AppDaemon: `/addon_configs/a0d7b954_appdaemon/`

### Zainstalowane integracje
- **SolaX Inverter Modbus** — integracja Sofar HYD przez Modbus
- **AppDaemon** (add-on) — środowisko Python, folder apps: `/addon_configs/a0d7b954_appdaemon/apps/`
- **TinyTuya** — biblioteka Python w AppDaemon (`python_packages: tinytuya` w konfiguracji)
- **Pstryk AiO** — 16 encji, dynamiczna cena energii
- **Xtend Tuya** — 12 urządzeń
- **Open-Meteo** — pogoda
- **Solarman** — monitoring falownika (SolarMAN portal)
- **SmartThings**, **Miele**, **Mobile App**, **RESTful**

### Kluczowe encje — Sofar HYD (przez SolaX Inverter Modbus)

```
sensor.sofar_modbus_battery_1_1_soc           # SOC baterii [%]
sensor.sofar_modbus_inverter_pv_power_total   # Moc PV [W] (suma obu MPPT)
sensor.sofar_modbus_inverter_active_power_load_sys  # Zużycie domu [W]
sensor.sofar_modbus_inverter_active_power_pcc_total # Moc PCC [W]
  # UWAGA: znak PCC: dodatni = eksport do sieci, ujemny = import z sieci
  # Ten znak ZMIENIA SIĘ po zmianie trybu pracy Sofara! Zawsze weryfikuj.

select.sofar_modbus_inverter_energy_storage_mode  # Tryb pracy (Time of Use / Passive / Self-use)
number.sofar_modbus_inverter_passive_desired_grid_power   # Passive Mode: żądana moc PCC
number.sofar_modbus_inverter_passive_maximum_battery_power
number.sofar_modbus_inverter_passive_minimum_battery_power
button.sofar_modbus_inverter_update_passive_parameters    # Zatwierdź parametry Passive Mode
```

### Kluczowe encje — Pstryk
```
sensor.pstryk_energy_pstryk_current_buy_price  # Aktualna cena zakupu [zł/kWh]
```

### Kluczowe encje — Ładowarka EV (tworzone przez AppDaemon)
```
input_text.ev_charger_status     # Status ładowarki (WORKING/SLEEP/IDLE/UNKNOWN)
input_text.ev_charger_mode       # Tryb automatyki (SOLAR/NEGATIVE_PRICE/BATTERY_PRIORITY/IDLE/WINTER_NIGHT)
input_number.ev_charger_power    # Moc ładowania [W]
input_number.ev_charger_current  # Prąd ładowania [A]
input_number.ev_session_kwh      # Energia bieżącej sesji [kWh]
input_number.ev_month_kwh        # Energia w bieżącym miesiącu [kWh]
input_number.ev_total_kwh        # Energia łączna [kWh]
input_boolean.ev_tryb_zimowy     # Przełącznik sezonowy (zima = nocne ładowanie)
```

**WAŻNE**: Helpery (input_text, input_number, input_boolean) muszą być tworzone przez UI HA (Settings → Helpers), NIE przez configuration.yaml. Encje zdefiniowane w YAML są read-only dla serwisów API i nie można ich modyfikować programowo.

### Template sensory w configuration.yaml
```yaml
# Fragment z /config/configuration.yaml
# Sensory prezentacyjne bazujące na helperach
template:
  - sensor:
      - name: "EV Status"
        state: "{{ states('input_text.ev_charger_status') | upper }}"
      - name: "EV Mode"
        state: "{{ states('input_text.ev_charger_mode') | upper }}"
      - name: "EV Power W"
        state: "{{ states('input_number.ev_charger_power') | float(0) | round(0) }}"
        unit_of_measurement: "W"
      - name: "EV Current A"
        state: "{{ states('input_number.ev_charger_current') | float(0) | round(0) }}"
        unit_of_measurement: "A"
      - name: "EV Session kWh"
        state: "{{ states('input_number.ev_session_kwh') | float(0) | round(3) }}"
        unit_of_measurement: "kWh"
      - name: "EV Month kWh"
        state: "{{ states('input_number.ev_month_kwh') | float(0) | round(2) }}"
        unit_of_measurement: "kWh"
      - name: "EV Total kWh"
        state: "{{ states('input_number.ev_total_kwh') | float(0) | round(2) }}"
        unit_of_measurement: "kWh"
```

### Tryb pracy falownika
Sofar HYD jest ustawiony na **Time of Use (zawsze)**. Zmiana przez:
```
select.sofar_modbus_inverter_energy_storage_mode → "Time of Use"
```

---

## 4. APPDAEMON — KONFIGURACJA

### Ścieżki
```
/addon_configs/a0d7b954_appdaemon/appdaemon.yaml   # Główna konfiguracja
/addon_configs/a0d7b954_appdaemon/apps/             # Folder aplikacji
/addon_configs/a0d7b954_appdaemon/apps/apps.yaml    # Rejestr aplikacji
/addon_configs/a0d7b954_appdaemon/apps/ev_charger.py  # Główny skrypt
/addon_configs/a0d7b954_appdaemon/apps/hello_world.py  # Testowa aplikacja
```

### Fragment appdaemon.yaml (python_packages)
```yaml
appdaemon:
  # ... inne ustawienia ...
  python_packages:
    - tinytuya
```
TinyTuya instaluje się automatycznie przy każdym starcie AppDaemon.

### apps.yaml
```yaml
hello_world:
  module: hello
  class: HelloWorld

ev_charger_control:
  module: ev_charger
  class: EVChargerControl
```

---

## 5. ŁADOWARKA EV — DATAPOINTS (DP) TUYA

### Kluczowe DP
| DP | Typ | Opis |
|-----|-----|------|
| `109` | string | Status: `WORKING` / `SLEEP` / `IDLE` |
| `150` | int | Prąd ładowania [A], zakres 6–16 |
| `102` | JSON string | Dane pomiarowe (moc x100W per faza) |
| `140` | bool | Start/Stop: `true` = start, `false` = stop |
| `151` | JSON string | Harmonogram — **WAŻNE: wyczyść przy starcie!** |

### WAŻNE pułapki DP

**1. Klucze DP jako stringi, nie integery:**
```python
# ŹLE:
dps.get(109, "unknown")
# DOBRZE:
dps.get("109", "unknown")
# lub:
dps.get(str(DP_STATUS), "unknown")
```

**2. DP 102 — moc mnożona przez 100:**
```python
metrics = json.loads(dps.get("102", "{}"))
# Pole "p" zawiera łączną moc w dziesiątkach watów * 10 (mnożnik x100)
# Przykład: p=97 → 9700W
```

**3. DP 151 (harmonogram) blokuje START:**
Jeśli harmonogram jest ustawiony, ładowarka ignoruje polecenie START przez DP 140.
Rozwiązanie: wyczyść harmonogram przy starcie skryptu:
```python
import json
device.set_value("151", json.dumps({"m": 0, "dt": 0, "ss": "00:00", "se": "00:00"}))
```

**4. DP 150 (prąd) — ustaw przed START:**
Zawsze ustaw prąd przed wysłaniem polecenia START, bo ładowarka może wystartować z poprzednim prądem.

**5. PAUSE wymaga wyczyszczenia harmonogramu:**
Samo `set_value("140", False)` zatrzymuje ładowanie, ale ustawia tryb PAUSE.
Żeby zrestartować po PAUSE, należy wyczyścić harmonogram (DP 151).

---

## 6. GŁÓWNY SKRYPT — ev_charger.py

### Plik: `/addon_configs/a0d7b954_appdaemon/apps/ev_charger.py`

```python
import appdaemon.plugins.hass.hassapi as hass
import tinytuya
import json
import datetime

DEVICE_ID  = "bffc3e022bd8ef987fhias"
DEVICE_IP  = "192.168.50.250"
DEVICE_KEY = "h|p4Nv.-V/U7@Oa3"
PROTOCOL   = 3.5

DP_STATUS  = 109
DP_CURRENT = 150
DP_METRICS = 102
DP_SWITCH  = 140

SOC_THRESHOLD    = 95
MIN_CURRENT_A    = 6
MAX_CURRENT_A    = 16
PHASES           = 3
VOLTAGE          = 230
START_SURPLUS_W  = 5000
STOP_SURPLUS_W   = 3500
NEGATIVE_PRICE_THRESHOLD = 0.0
UPDATE_INTERVAL_S = 30

# Tryb zimowy - ladowanie nocne
WINTER_MODE_ENTITY  = "input_boolean.ev_tryb_zimowy"
WINTER_MAX_CURRENT  = 10
WINTER_START_HOUR   = 22
WINTER_END_HOUR     = 6

SENSOR_SOC        = "sensor.sofar_modbus_battery_1_1_soc"
SENSOR_PV_POWER   = "sensor.sofar_modbus_inverter_pv_power_total"
SENSOR_LOAD_POWER = "sensor.sofar_modbus_inverter_active_power_load_sys"
SENSOR_GRID_POWER = "sensor.sofar_modbus_inverter_active_power_pcc_total"
SENSOR_PRICE      = "sensor.pstryk_energy_pstryk_current_buy_price"


class EVChargerControl(hass.Hass):

    def initialize(self):
        self.log("EV Charger Control startuje...")
        self._charger_active = False
        self._current_session_kwh = 0.0
        self._month_energy_kwh = self._load_persistent("ev_month_energy_kwh", 0.0)
        self._total_energy_kwh = self._load_persistent("ev_total_energy_kwh", 0.0)
        self._last_month = datetime.datetime.now().month
        self._last_update_time = None
        self._last_power_w = 0.0
        self._session_start_time = None
        self._device_error_count = 0
        self._last_sent_current = -1
        self._last_sent_switch = None

        self._device = tinytuya.Device(
            DEVICE_ID, DEVICE_IP, DEVICE_KEY, version=PROTOCOL
        )
        self._device.set_socketTimeout(6)
        self._device.set_socketRetryLimit(3)

        self._clear_schedule()
        self.run_every(self._main_loop, "now", UPDATE_INTERVAL_S)
        self.log("EV Charger Control zainicjalizowany")

    def _main_loop(self, kwargs):
        charger_data = self._get_charger_data()
        self._update_energy_counters(charger_data)
        ha_data = self._get_ha_data()
        mode, target_current = self._decide(ha_data, charger_data)
        self._apply_decision(mode, target_current, charger_data)
        self._update_sensors(charger_data, ha_data, mode, target_current)
        self._update_ha_helpers(charger_data, mode)

    def _get_charger_data(self):
        try:
            raw = self._device.status()
            if not raw.get('dps', {}).get('109'):
                raw = self._device.status()
            dps = raw.get("dps", {})
            status  = str(dps.get(str(DP_STATUS), "unknown")).upper()
            current = int(dps.get(str(DP_CURRENT), 0))
            metrics_raw = dps.get(str(DP_METRICS), "{}")
            try:
                metrics = json.loads(metrics_raw) if isinstance(metrics_raw, str) else metrics_raw
            except (json.JSONDecodeError, TypeError):
                metrics = {}
            l1 = metrics.get("L1", [0, 0, 0])
            l2 = metrics.get("L2", [0, 0, 0])
            l3 = metrics.get("L3", [0, 0, 0])
            p1 = l1[2] if len(l1) > 2 else 0
            p2 = l2[2] if len(l2) > 2 else 0
            p3 = l3[2] if len(l3) > 2 else 0
            power_w = (p1 + p2 + p3) * 100 if status == "WORKING" else 0
            self._device_error_count = 0
            return {"status": status, "current_a": current, "power_w": power_w, "metrics": metrics, "online": True}
        except Exception as e:
            self._device_error_count += 1
            if self._device_error_count <= 3:
                self.log(f"Blad polaczenia z ladowarka: {e}", level="WARNING")
            return {"status": "offline", "current_a": 0, "power_w": 0, "metrics": {}, "online": False}

    def _get_ha_data(self):
        def safe_float(entity_id, default=0.0):
            try:
                val = self.get_state(entity_id)
                return float(val) if val not in (None, "unknown", "unavailable") else default
            except (TypeError, ValueError):
                return default
        soc        = safe_float(SENSOR_SOC)
        pv_power   = safe_float(SENSOR_PV_POWER)
        load_power = safe_float(SENSOR_LOAD_POWER)
        grid_power = safe_float(SENSOR_GRID_POWER)
        price      = safe_float(SENSOR_PRICE, default=9.99)
        surplus_w  = max(0, grid_power * 1000)
        return {"soc": soc, "pv_power": pv_power * 1000, "load_power": load_power * 1000,
                "grid_power": grid_power, "surplus_w": surplus_w, "price": price}

    def _decide(self, ha_data, charger_data):
        if not charger_data["online"]:
            return ("OFFLINE", 0)
        price   = ha_data["price"]
        soc     = ha_data["soc"]
        surplus = ha_data["surplus_w"]

        if price < NEGATIVE_PRICE_THRESHOLD:
            self.log(f"Tryb NEGATIVE_PRICE: cena={price:.2f} zl/kWh")
            return ("NEGATIVE_PRICE", MAX_CURRENT_A)

        winter_mode = self.get_state(WINTER_MODE_ENTITY) == "on"
        if winter_mode:
            now_hour = datetime.datetime.now().hour
            in_night_window = (now_hour >= WINTER_START_HOUR or now_hour < WINTER_END_HOUR)
            if in_night_window:
                self.log(f"Tryb WINTER_NIGHT: godzina={now_hour}, prad={WINTER_MAX_CURRENT}A")
                return ("WINTER_NIGHT", WINTER_MAX_CURRENT)
            # Poza oknem nocnym - kontynuuj normalnie (SOLAR/BATTERY_PRIORITY)

        if soc < SOC_THRESHOLD:
            self.log(f"Tryb BATTERY_PRIORITY: SOC={soc:.0f}%")
            return ("BATTERY_PRIORITY", 0)

        charger_working = charger_data["status"] == "WORKING"
        charger_power_w = charger_data["power_w"]

        if charger_working:
            available_surplus = surplus + charger_power_w
            if available_surplus < STOP_SURPLUS_W and surplus < 500:
                available_surplus = STOP_SURPLUS_W + 100  # nie zatrzymuj gdy bilans idealny
        else:
            available_surplus = surplus
        available_surplus = max(0, available_surplus)

        self.log(
            f"SOC={soc:.0f}%, PV={ha_data['pv_power']:.0f}W, "
            f"eksport={surplus:.0f}W, nadwyzka={available_surplus:.0f}W, "
            f"auto={'WORKING' if charger_working else 'STOP'}, cena={price:.2f}"
        )

        if not self._charger_active:
            if available_surplus >= START_SURPLUS_W:
                target = self._surplus_to_current(available_surplus)
                self.log(f"Tryb SOLAR: wlaczam {target}A")
                return ("SOLAR", target)
            else:
                return ("IDLE", 0)
        else:
            if available_surplus < STOP_SURPLUS_W:
                self.log(f"Tryb SOLAR->IDLE: nadwyzka={available_surplus:.0f}W za mala")
                return ("IDLE", 0)
            else:
                target = self._surplus_to_current(available_surplus)
                self.log(f"Tryb SOLAR: reguluje do {target}A")
                return ("SOLAR", target)

    def _surplus_to_current(self, surplus_w):
        current = surplus_w / (PHASES * VOLTAGE)
        return max(MIN_CURRENT_A, min(MAX_CURRENT_A, int(current)))

    def _apply_decision(self, mode, target_current, charger_data):
        charger_status = charger_data["status"]
        if mode in ("NEGATIVE_PRICE", "SOLAR", "WINTER_NIGHT"):
            if charger_status in ("IDLE", "UNKNOWN"):
                self._charger_active = False
                return
            if target_current > 0 and target_current != self._last_sent_current:
                self._set_current(target_current)
                self._last_sent_current = target_current
            if not self._charger_active and self._last_sent_switch != True:
                self._set_switch(True)
                self._last_sent_switch = True
                self._charger_active = True
                if self._session_start_time is None:
                    self._session_start_time = datetime.datetime.now()
                    self._current_session_kwh = 0.0
        elif mode in ("BATTERY_PRIORITY", "IDLE", "OFFLINE"):
            if charger_status == "WORKING":
                self._set_switch(False)
                self._last_sent_switch = False
                self._charger_active = False
                self._session_start_time = None

    def _clear_schedule(self):
        """Czysc harmonogram ladowarki przy starcie - zapobiega PAUSE."""
        try:
            import json
            self._device.set_value("151", json.dumps({"m":0,"dt":0,"ss":"00:00","se":"00:00"}))
            self.log("Harmonogram ladowarki wyczyszczony")
        except Exception as e:
            self.log(f"Blad czyszczenia harmonogramu: {e}", level="WARNING")

    def _set_current(self, current_a):
        try:
            self._device.set_value(DP_CURRENT, current_a)
            self.log(f"Ustawiono prad: {current_a}A")
        except Exception as e:
            self.log(f"Blad ustawiania pradu: {e}", level="ERROR")

    def _set_switch(self, on: bool):
        try:
            self._device.set_value(DP_SWITCH, on)
            self.log(f"Ladowarka: {'START' if on else 'STOP'}")
        except Exception as e:
            self.log(f"Blad przelaczania: {e}", level="ERROR")

    def _update_energy_counters(self, charger_data):
        now = datetime.datetime.now()
        if now.month != self._last_month:
            self.log(f"Nowy miesiac! Reset: {self._month_energy_kwh:.2f} kWh")
            self._month_energy_kwh = 0.0
            self._last_month = now.month
        if self._last_update_time is not None and charger_data["online"]:
            dt_hours = (now - self._last_update_time).total_seconds() / 3600.0
            power_w  = charger_data["power_w"]
            energy_kwh = (power_w * dt_hours) / 1000.0
            if energy_kwh > 0:
                self._current_session_kwh += energy_kwh
                self._month_energy_kwh    += energy_kwh
                self._total_energy_kwh    += energy_kwh
                self._save_persistent("ev_month_energy_kwh", self._month_energy_kwh)
                self._save_persistent("ev_total_energy_kwh", self._total_energy_kwh)
            if charger_data["status"] != "WORKING" and self._session_start_time is not None:
                duration = (now - self._session_start_time).total_seconds() / 60.0
                self.log(f"Sesja zakonczona: {self._current_session_kwh:.2f} kWh, czas: {duration:.0f} min")
                self._session_start_time = None
        self._last_update_time = now
        self._last_power_w = charger_data["power_w"]

    def _update_sensors(self, charger_data, ha_data, mode, target_current):
        self.log(
            f"Sensory: status={charger_data['status']}, moc={charger_data['power_w']:.0f}W, "
            f"tryb={mode}, sesja={self._current_session_kwh:.3f}kWh"
        )

    def _save_persistent(self, key, value):
        try:
            import json, os
            path = "/config/ev_charger_data.json"
            data = {}
            if os.path.exists(path):
                with open(path, "r") as f:
                    data = json.load(f)
            data[key] = value
            with open(path, "w") as f:
                json.dump(data, f)
        except Exception as e:
            self.log(f"Blad zapisu persistent: {e}", level="WARNING")

    def _load_persistent(self, key, default):
        try:
            import json, os
            path = "/config/ev_charger_data.json"
            if os.path.exists(path):
                with open(path, "r") as f:
                    data = json.load(f)
                return float(data.get(key, default))
            return default
        except Exception as e:
            self.log(f"Blad odczytu persistent: {e}", level="WARNING")
            return default

    def _update_ha_helpers(self, charger_data, mode):
        try:
            import json
            data = json.dumps({
                "status": charger_data["status"],
                "mode": mode,
                "power": round(charger_data["power_w"], 0),
                "current": charger_data["current_a"],
                "session": round(self._current_session_kwh, 3),
                "month": round(self._month_energy_kwh, 3),
                "total": round(self._total_energy_kwh, 3)
            })
            self.call_service("input_text/set_value",
                entity_id="input_text.ev_charger_status",
                value=charger_data["status"])
            self.call_service("input_text/set_value",
                entity_id="input_text.ev_charger_mode",
                value=mode)
            self.call_service("input_text/set_value",
                entity_id="input_text.ev_data",
                value=data)
        except Exception as e:
            self.log(f"Blad zapisu helperow: {e}", level="WARNING")
```

---

## 7. AKTYWNE AUTOMATYZACJE HA

### 1. Powiadomienie przy nadwyżkach PV (gdy auto odłączone)
Wyzwalacz: SOC >= 90% I PV > 2kW
Akcja: powiadomienie na telefon "Można ładować auto z nadwyżek PV"

### 2. Powiadomienie przy ujemnej cenie Pstryk
Wyzwalacz: `sensor.pstryk_energy_pstryk_current_buy_price` < 0
Akcja: powiadomienie "Ujemna cena energii Pstryk!"

### 3. Aktualizacja cen Pstryk
Wyzwalacz: codziennie o określonej godzinie
Akcja: wywołanie serwisu aktualizacji cen Pstryk AiO

---

## 8. PLAN TARYFY

- **Teraz (lato 2026):** Pstryk (dynamiczna) — korzystne bo ujemne ceny przy nadwyżkach PV
- **Sierpień 2026:** Zgłoszenie zmiany taryfy do PGE
- **Październik 2026:** Przejście na **G12W** — tańsza nocna (~0.70 zł vs ~0.82 zł Pstryk)
- **Powód zmiany:** Latem Pstryk nieopłacalny (ujemne ceny gdy PV produkuje = nie można dużo pobrać z sieci)

---

## 9. ZNANE PROBLEMY I ROZWIĄZANIA

| Problem | Rozwiązanie |
|---------|------------|
| Local Tuya nie działa z ładowarką | Protokół Tuya 3.5 — Local Tuya obsługuje tylko 3.4. Jedyne rozwiązanie: TinyTuya przez AppDaemon |
| DP klucze jako stringi | Zawsze `dps.get(str(DP_STATUS))`, nie `dps.get(109)` |
| DP 102 moc w nieoczekiwanej skali | Wartość p mnożona x100 (np. 97 = 9700W) |
| Harmonogram DP151 blokuje START | Wyczyść harmonogram przy initialize() |
| Znak PCC Sofar się zmienia | Po zmianie trybu (np. z Time of Use na Passive) znak PCC może się odwrócić — weryfikuj |
| HA 2026.x: `set_state()` zwraca 400 | Nie używaj `set_state()` dla helperów. Używaj `call_service("input_text/set_value", ...)` |
| Helpery read-only po definicji w YAML | Twórz helpery TYLKO przez UI HA, nie przez configuration.yaml |
| AppDaemon timeout | `set_socketTimeout(5)` + `set_socketRetryLimit(2)` na urządzeniu TinyTuya |
| Częste włączanie/wyłączanie | Histeria: włącz przy 5kW nadwyżki, wyłącz dopiero przy 3.5kW |

---

## 10. ARTYKUŁ NA BLOGU

**Tytuł:** "Jak za 1150 zł zrobiłem inteligentne ładowanie auta elektrycznego z nadwyżek słońca"
**URL:** tomaszkwietniewski.pl
**Plik MD:** `ladowanie_ev_z_nadwyzek_pv.md`

Kluczowe sekcje artykułu:
1. Wstęp — ładowarka bez API
2. Architektura rozwiązania (TinyTuya + AppDaemon)
3. Odkrywanie DataPoints przez wireshark/tuya-cli
4. Pułapki techniczne (patrz tabela wyżej)
5. Logika sterowania i tryby
6. Strategia sezonowa (lato/zima)
7. Efekty i wnioski

---

## 11. JAK UŻYWAĆ TEGO PLIKU W WINDSURF / CLAUDE CODE

1. Umieść ten plik w katalogu projektu jako `CONTEXT.md`
2. W rozmowie z Claude Code napisz: *"Przeczytaj CONTEXT.md — to dokumentacja mojego projektu domowej automatyki EV"*
3. Claude Code będzie miał pełen kontekst bez potrzeby tłumaczenia od zera

### Typowe zadania do kontynuacji:
- Aktualizacja artykułu na blogu (plik `ladowanie_ev_z_nadwyzek_pv.md`)
- Debugowanie skryptu `ev_charger.py` (sprawdź logi: `ha apps logs a0d7b954_appdaemon`)
- Dodanie nowej logiki (np. integracja z Citroen Stellantis API dla SOC auta)
- Przejście na taryfę G12W (październik 2026) — aktualizacja logiki nocnego ładowania
