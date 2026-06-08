import appdaemon.plugins.hass.hassapi as hass
import tinytuya
import json
import datetime
import os

# Dane urządzenia ładowarki — czytane z osobnego pliku secrets.
# UWAGA na mapowanie ścieżek AppDaemon: w środowisku add-onu "/config/"
# mapuje się na katalog add-onu (/addon_configs/a0d7b954_appdaemon/),
# NIE na główny katalog HA (/config/). Aktywny plik sekretów to:
#   /addon_configs/a0d7b954_appdaemon/ev_charger_secrets.json
_SECRETS_PATH = "/config/ev_charger_secrets.json"
try:
    with open(_SECRETS_PATH) as _f:
        _secrets = json.load(_f)
    DEVICE_ID  = _secrets["device_id"]
    DEVICE_IP  = _secrets["device_ip"]
    DEVICE_KEY = _secrets["device_key"]
except FileNotFoundError:
    raise RuntimeError(f"Brak pliku {_SECRETS_PATH} — skopiuj .example i uzupełnij danymi!")
except KeyError as e:
    raise RuntimeError(f"Brakuje klucza {e} w {_SECRETS_PATH}")

PROTOCOL   = 3.5

DP_STATUS  = 109
DP_CURRENT = 150
DP_METRICS = 102
DP_SWITCH  = 140

# --- Progi SOC baterii ---
SOC_THRESHOLD     = 95   # [%] poniżej - nie ładuj auta (ochrona baterii)
SOC_EMERGENCY_MIN = 20   # [%] w trybie EMERGENCY zatrzymaj gdy SOC spadnie poniżej

# --- Prąd ładowania ---
MIN_CURRENT_A       = 6    # [A] minimum wymagane przez ładowarkę
MAX_CURRENT_A       = 16   # [A] maksimum ładowarki
EMERGENCY_CURRENT_A = 13   # [A] tryb emergency (~9kW, bufor ~2kW na dom przy 11kW przyłączu)
PHASES              = 3
VOLTAGE             = 230

# --- Progi nadwyżki solarnej (z uwzględnieniem SURPLUS_BIAS_W) ---
START_SURPLUS_W = 1600   # [W] min nadwyżka (po doliczeniu biasu) do startu
STOP_SURPLUS_W  = 1200   # [W] poniżej - zatrzymaj ładowanie (histereza)

# Bufor zachęcający do startu: doliczany do realnej nadwyżki PCC.
# Dzięki temu auto startuje już przy ~0.6 kW realnego eksportu (1.6 - 1.0)
# zamiast czekać na pełne 1.6 kW. Gdy importujemy, surplus_w = SURPLUS_BIAS_W.
SURPLUS_BIAS_W = 1000

# --- Uśrednianie PCC (wygładzanie migotania) ---
PCC_HISTORY_SIZE = 3     # ile ostatnich odczytów uśredniać (3 * 30s = 90s)

# --- Cena energii ---
NEGATIVE_PRICE_THRESHOLD = 0.0

# --- Interwał pętli ---
UPDATE_INTERVAL_S = 30

# --- Watchdog zamrożonego DP 102 (firmware quirk dé EV v2.9.4) ---
# Gdy w aktywnym trybie ładowania status=WORKING ale moc=0W przez N
# iteracji — wallbox prawdopodobnie zamroził pomiar. Lekarstwo:
# Reboot z aplikacji Smart Life. Watchdog tylko ostrzega w logach.
WATCHDOG_FROZEN_DP_THRESHOLD = 20  # 20 × 30s = 10 minut

# --- Tryb zimowy ---
WINTER_MODE_ENTITY  = "input_boolean.ev_tryb_zimowy"
WINTER_MAX_CURRENT  = 10
WINTER_START_HOUR   = 22
WINTER_END_HOUR     = 6

# --- Tryb EMERGENCY ---
EMERGENCY_MODE_ENTITY  = "input_boolean.ev_tryb_awaryjny"
EMERGENCY_HOURS_ENTITY = "input_number.ev_awaryjny_godziny"

# --- Sensory Sofar ---
SENSOR_SOC        = "sensor.sofar_modbus_battery_1_1_soc"
SENSOR_PV_POWER   = "sensor.sofar_modbus_inverter_pv_power_total"
SENSOR_LOAD_POWER = "sensor.sofar_modbus_inverter_active_power_load_sys"
SENSOR_GRID_POWER = "sensor.sofar_modbus_inverter_active_power_pcc_total"
SENSOR_PRICE      = "sensor.pstryk_energy_pstryk_current_buy_price"

# --- Archiwum historii miesięcznej ---
# Liczniki utility_meter (zerują się 1. dnia miesiąca). Snapshot ich
# wartości robimy w każdej iteracji — przy przełomie miesiąca archiwizujemy
# snapshot z POPRZEDNIEJ iteracji (czyli stan na koniec starego miesiąca),
# co uniezależnia nas od kolejności resetu utility_meter względem pętli AppDaemon.
SENSOR_UM_PRODUKCJA = "sensor.produkcja_pv_miesiac"
SENSOR_UM_ZUZYCIE   = "sensor.zuzycie_domu_miesiac"
SENSOR_UM_IMPORT    = "sensor.import_z_sieci_miesiac"
SENSOR_UM_EKSPORT   = "sensor.eksport_do_sieci_miesiac"

# Sensor publikowany przez AppDaemon — atrybut "months" zawiera całe archiwum.
HISTORY_SENSOR     = "sensor.ev_historia_miesieczna"
HISTORY_MAX_MONTHS = 120   # ile miesięcy trzymamy (10 lat)

# Stany ładowarki
CHARGER_READY_STATES   = {"PAUSE", "SLEEP", "IDLE", "UNKNOWN"}
CHARGER_WORKING_STATES = {"WORKING"}


class EVChargerControl(hass.Hass):

    def initialize(self):
        self.log("EV Charger Control startuje...")
        self._charger_active      = False
        self._current_session_kwh = 0.0
        self._month_energy_kwh    = self._load_persistent("ev_month_energy_kwh", 0.0)
        self._total_energy_kwh    = self._load_persistent("ev_total_energy_kwh", 0.0)
        # Znacznik miesiąca "YYYY-MM" — trwały, by archiwizacja zadziałała nawet
        # gdy AppDaemon wstanie dopiero po przełomie miesiąca (np. reboot NAS 1.dnia).
        self._last_ym             = self._load_persistent_raw(
            "ev_last_ym", datetime.datetime.now().strftime("%Y-%m"))
        self._um_snapshot         = {}   # ostatnio widziane wartości utility_meter
        self._last_update_time    = None
        self._last_power_w        = 0.0
        self._session_start_time  = None
        self._device_error_count  = 0
        self._last_sent_current   = -1
        self._last_sent_switch    = None
        self._emergency_end_time  = None
        self._pcc_history         = []
        # --- DIAG bug 2: śledzenie czy wallbox tkwi w WORKING+0W i czy DP 151 się zmienia ---
        self._working_zero_power_streak = 0
        self._last_schedule_seen        = None

        # Odtwórz sensor historii z trwałego pliku (po restarcie HA/AppDaemon)
        self._publish_history()

        self._device = tinytuya.Device(
            DEVICE_ID, DEVICE_IP, DEVICE_KEY, version=PROTOCOL
        )
        self._device.set_socketTimeout(6)
        self._device.set_socketRetryLimit(3)

        # --- DIAG bug 2: jednorazowy dump wszystkich DP na starcie ---
        # Cel: discovery — może istnieje alternatywne pole z pomiarem mocy
        # (DP 17 / DP 110 / inne typowe dla mierników Tuya), którego nie używamy.
        try:
            init_raw = self._device.status()
            init_dps = init_raw.get("dps", {})
            self.log(f"DIAG INIT: pelny DPS = {json.dumps(init_dps, ensure_ascii=False)}")
        except Exception as e:
            self.log(f"DIAG INIT: nie udalo sie pobrac pelnego DPS: {e}", level="WARNING")

        self._clear_schedule()
        self.listen_state(self._on_emergency_toggle, EMERGENCY_MODE_ENTITY)
        self.run_every(self._main_loop, "now", UPDATE_INTERVAL_S)
        self.log("EV Charger Control zainicjalizowany")

    # ------------------------------------------------------------------
    # EMERGENCY
    # ------------------------------------------------------------------

    def _on_emergency_toggle(self, entity, attribute, old, new, kwargs):
        if new == "on":
            hours = self._get_emergency_hours()
            self._emergency_end_time = datetime.datetime.now() + datetime.timedelta(hours=hours)
            self.log(f"EMERGENCY START: {hours}h, koniec o {self._emergency_end_time.strftime('%H:%M')}")
            self._clear_schedule()
            self._last_sent_switch  = None
            self._last_sent_current = -1
        else:
            self._emergency_end_time = None
            self.log("EMERGENCY STOP: powrót do trybu normalnego")
            self._charger_active   = False
            self._last_sent_switch = None

    def _get_emergency_hours(self):
        try:
            val = self.get_state(EMERGENCY_HOURS_ENTITY)
            return float(val) if val not in (None, "unknown", "unavailable") else 2.0
        except (TypeError, ValueError):
            return 2.0

    def _is_emergency_active(self):
        if self.get_state(EMERGENCY_MODE_ENTITY) != "on":
            return False
        if self._emergency_end_time is None:
            self.call_service("input_boolean/turn_off", entity_id=EMERGENCY_MODE_ENTITY)
            return False
        if datetime.datetime.now() > self._emergency_end_time:
            self.log("EMERGENCY: czas minął, wyłączam tryb awaryjny")
            self.call_service("input_boolean/turn_off", entity_id=EMERGENCY_MODE_ENTITY)
            self._emergency_end_time = None
            return False
        return True

    # ------------------------------------------------------------------
    # GŁÓWNA PĘTLA
    # ------------------------------------------------------------------

    def _main_loop(self, kwargs):
        charger_data = self._get_charger_data()
        self._update_energy_counters(charger_data)
        ha_data = self._get_ha_data()
        mode, target_current = self._decide(ha_data, charger_data)
        self._apply_decision(mode, target_current, charger_data)
        self._update_diag(charger_data, mode)
        self._update_sensors(charger_data, ha_data, mode, target_current)
        self._update_ha_helpers(charger_data, ha_data, mode, target_current)

    # ------------------------------------------------------------------
    # WATCHDOG / DIAGNOSTYKA
    # ------------------------------------------------------------------

    def _update_diag(self, charger_data, mode):
        """Watchdog na zamrożenie pomiaru DP 102 + detekcja zmian DP 151.

        Bug poznawczy z 2026-05-21: firmware dé EV Charger v2.9.4 potrafi
        "zamrozić" DP 102 (cały blok pomiarów L1/L2/L3/p/e/t) na wiele
        godzin — wallbox raportuje WORKING + moc 0W mimo realnego ładowania.
        Soft Reboot z aplikacji Smart Life naprawia. Skrypt nie wykrywa
        problemu sam, ale ten watchdog ostrzeże w logach gdy WORKING+0W
        utrzymuje się długo w aktywnym trybie ładowania.
        """
        # Streak tylko gdy skrypt świadomie chce ładować (nie liczymy
        # przerw IDLE/BATTERY_PRIORITY, gdzie 0W jest oczekiwane).
        active_charging_modes = ("SOLAR", "EMERGENCY", "NEGATIVE_PRICE", "WINTER_NIGHT")
        in_active_mode = mode in active_charging_modes
        worker_no_power = (charger_data["status"] in CHARGER_WORKING_STATES
                           and charger_data["power_w"] == 0)

        if in_active_mode and worker_no_power:
            self._working_zero_power_streak += 1
            # Watchdog WARNING — uderza raz, dokładnie przy przekroczeniu progu.
            if self._working_zero_power_streak == WATCHDOG_FROZEN_DP_THRESHOLD:
                self.log(
                    f"WATCHDOG: WORKING+0W w trybie {mode} przez "
                    f"{WATCHDOG_FROZEN_DP_THRESHOLD * UPDATE_INTERVAL_S}s. "
                    f"Prawdopodobne zamrożenie DP 102 w wallboxie — "
                    f"sprobuj Reboot z Smart Life. "
                    f"DP102_raw={charger_data.get('metrics_raw')!r}",
                    level="WARNING"
                )
        else:
            if self._working_zero_power_streak >= WATCHDOG_FROZEN_DP_THRESHOLD:
                self.log(
                    f"WATCHDOG: koniec WORKING+0W "
                    f"({self._working_zero_power_streak} iteracji)"
                )
            self._working_zero_power_streak = 0

        # DP 151 — historia: chmura Tuya potrafi wpychać harmonogram
        # (zauważone po reboocie 2026-05-21: pojawił się "ss":"15:00","se":"17:00").
        # m:0 oznacza nieaktywny harmonogram, więc na razie nie blokuje.
        # Logujemy każdą zmianę żeby mieć ślad.
        schedule_now = charger_data.get("schedule")
        if schedule_now != self._last_schedule_seen:
            self.log(
                f"DIAG: DP151 zmiana: {self._last_schedule_seen!r} -> {schedule_now!r}"
            )
            self._last_schedule_seen = schedule_now

    # ------------------------------------------------------------------
    # ODCZYT DANYCH
    # ------------------------------------------------------------------

    def _get_charger_data(self):
        try:
            raw = self._device.status()
            if not raw.get('dps', {}).get('109'):
                raw = self._device.status()
            dps     = raw.get("dps", {})
            status  = str(dps.get(str(DP_STATUS), "unknown")).upper()
            current = int(dps.get(str(DP_CURRENT), 0))
            schedule_raw = dps.get("151", "")               # DIAG bug 2
            switch_raw   = dps.get(str(DP_SWITCH))          # DIAG bug 2
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
            power_w = (p1 + p2 + p3) * 100 if status in CHARGER_WORKING_STATES else 0
            self._device_error_count = 0
            return {"status": status, "current_a": current, "power_w": power_w,
                    "metrics": metrics, "online": True,
                    "schedule": schedule_raw, "switch": switch_raw,
                    "metrics_raw": metrics_raw}
        except Exception as e:
            self._device_error_count += 1
            if self._device_error_count <= 3:
                self.log(f"Blad polaczenia z ladowarka: {e}", level="WARNING")
            return {"status": "offline", "current_a": 0, "power_w": 0,
                    "metrics": {}, "online": False,
                    "schedule": None, "switch": None,
                    "metrics_raw": None}

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
        grid_power = safe_float(SENSOR_GRID_POWER)  # dodatni = eksport, ujemny = import
        price      = safe_float(SENSOR_PRICE, default=9.99)

        # Uśrednianie PCC — wygładzamy migotanie ±0.x kW przez ostatnie 3 odczyty (90s)
        self._pcc_history.append(grid_power)
        if len(self._pcc_history) > PCC_HISTORY_SIZE:
            self._pcc_history.pop(0)
        avg_pcc = sum(self._pcc_history) / len(self._pcc_history)

        # Sofar: dodatni PCC = eksport do sieci = nadwyżka PV.
        # Doliczamy SURPLUS_BIAS_W jako bufor zachęcający do startu (patrz definicja stałej).
        if avg_pcc > 0:
            surplus_w = avg_pcc * 1000 + SURPLUS_BIAS_W
        else:
            surplus_w = SURPLUS_BIAS_W

        return {
            "soc":        soc,
            "pv_power":   pv_power * 1000,
            "load_power": load_power * 1000,
            "grid_power": grid_power,
            "avg_pcc":    avg_pcc,
            "surplus_w":  surplus_w,
            "price":      price,
        }

    # ------------------------------------------------------------------
    # DECYZJA
    # ------------------------------------------------------------------

    def _decide(self, ha_data, charger_data):
        if not charger_data["online"]:
            return ("OFFLINE", 0)

        charger_status = charger_data["status"]

        # Auto niepodłączone
        if (charger_status not in CHARGER_READY_STATES
                and charger_status not in CHARGER_WORKING_STATES):
            return ("IDLE", 0)

        price   = ha_data["price"]
        soc     = ha_data["soc"]
        surplus = ha_data["surplus_w"]

        # 1. EMERGENCY — najwyższy priorytet
        if self._is_emergency_active():
            remaining_min = int(
                (self._emergency_end_time - datetime.datetime.now()).total_seconds() / 60
            )
            if soc < SOC_EMERGENCY_MIN:
                self.log(f"EMERGENCY: SOC={soc:.0f}% < {SOC_EMERGENCY_MIN}% — zatrzymuję")
                return ("BATTERY_PRIORITY", 0)
            self.log(f"Tryb EMERGENCY: prad={EMERGENCY_CURRENT_A}A, pozostalo={remaining_min}min")
            return ("EMERGENCY", EMERGENCY_CURRENT_A)

        # 2. Ujemna cena energii
        if price < NEGATIVE_PRICE_THRESHOLD:
            self.log(f"Tryb NEGATIVE_PRICE: cena={price:.2f} zl/kWh")
            return ("NEGATIVE_PRICE", MAX_CURRENT_A)

        # 3. Tryb zimowy — nocne ładowanie z sieci
        winter_mode = self.get_state(WINTER_MODE_ENTITY) == "on"
        if winter_mode:
            now_hour = datetime.datetime.now().hour
            in_night_window = (now_hour >= WINTER_START_HOUR or now_hour < WINTER_END_HOUR)
            if in_night_window:
                self.log(f"Tryb WINTER_NIGHT: godzina={now_hour}, prad={WINTER_MAX_CURRENT}A")
                return ("WINTER_NIGHT", WINTER_MAX_CURRENT)

        # 4. Ochrona baterii
        if soc < SOC_THRESHOLD:
            self.log(f"Tryb BATTERY_PRIORITY: SOC={soc:.0f}% < {SOC_THRESHOLD}%")
            return ("BATTERY_PRIORITY", 0)

        # 5. Tryb solarny
        charger_working = charger_status in CHARGER_WORKING_STATES
        charger_power_w = charger_data["power_w"]

        # Gdy ładujemy, pobór ładowarki jest już wliczony w load — dodaj do dostępnej nadwyżki
        if charger_working and charger_power_w > 0:
            available_surplus = surplus + charger_power_w
        else:
            available_surplus = surplus

        self.log(
            f"SOC={soc:.0f}%, PV={ha_data['pv_power']:.0f}W, "
            f"PCC={ha_data['grid_power']:.2f}kW (avg={ha_data['avg_pcc']:.2f}kW), "
            f"nadwyzka={available_surplus:.0f}W, "
            f"ladowarka={charger_status}, cena={price:.2f}"
        )

        if charger_working:
            if available_surplus < STOP_SURPLUS_W:
                self.log(f"SOLAR->IDLE: nadwyzka={available_surplus:.0f}W < {STOP_SURPLUS_W}W")
                return ("IDLE", 0)
            else:
                target = self._surplus_to_current(available_surplus)
                self.log(f"SOLAR: reguluję do {target}A")
                return ("SOLAR", target)
        else:
            if available_surplus >= START_SURPLUS_W:
                target = self._surplus_to_current(available_surplus)
                self.log(f"SOLAR: startuję {target}A (nadwyzka={available_surplus:.0f}W)")
                return ("SOLAR", target)
            else:
                return ("IDLE", 0)

    def _surplus_to_current(self, surplus_w):
        current = surplus_w / (PHASES * VOLTAGE)
        return max(MIN_CURRENT_A, min(MAX_CURRENT_A, int(current)))

    # ------------------------------------------------------------------
    # WYKONANIE DECYZJI
    # ------------------------------------------------------------------

    def _apply_decision(self, mode, target_current, charger_data):
        charger_status  = charger_data["status"]
        charger_working = charger_status in CHARGER_WORKING_STATES

        if mode in ("NEGATIVE_PRICE", "SOLAR", "WINTER_NIGHT", "EMERGENCY"):
            if target_current > 0 and target_current != self._last_sent_current:
                self._set_current(target_current)
                self._last_sent_current = target_current
            if not charger_working:
                if self._last_sent_switch != True:
                    self._clear_schedule()
                    self._set_switch(True)
                    self._last_sent_switch = True
                    self._charger_active   = True
                    if self._session_start_time is None:
                        self._session_start_time  = datetime.datetime.now()
                        self._current_session_kwh = 0.0
            else:
                self._charger_active = True

        elif mode in ("BATTERY_PRIORITY", "IDLE", "OFFLINE"):
            if charger_working:
                if self._last_sent_switch != False:
                    self._set_switch(False)
                    self._last_sent_switch = False
                self._charger_active     = False
                self._session_start_time = None
            elif self._charger_active:
                self._charger_active = False

    # ------------------------------------------------------------------
    # KOMUNIKACJA Z ŁADOWARKĄ
    # ------------------------------------------------------------------

    def _clear_schedule(self):
        """Czyść harmonogram ładowarki — zapobiega PAUSE."""
        try:
            self._device.set_value("151", json.dumps(
                {"m": 0, "dt": 0, "ss": "00:00", "se": "00:00"}
            ))
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

    # ------------------------------------------------------------------
    # LICZNIKI ENERGII
    # ------------------------------------------------------------------

    def _update_energy_counters(self, charger_data):
        now = datetime.datetime.now()
        current_ym = now.strftime("%Y-%m")
        if current_ym != self._last_ym:
            # Przełom miesiąca — zarchiwizuj zamknięty miesiąc PRZED wyzerowaniem.
            # _um_snapshot wciąż trzyma wartości z poprzedniej iteracji (stary miesiąc).
            self._archive_month(self._last_ym, self._month_energy_kwh)
            self.log(
                f"Nowy miesiac! Reset: {self._month_energy_kwh:.2f} kWh "
                f"(zarchiwizowano {self._last_ym})"
            )
            self._month_energy_kwh = 0.0
            self._last_ym = current_ym
            self._save_persistent("ev_last_ym", current_ym)
            self._save_persistent("ev_month_energy_kwh", self._month_energy_kwh)
        if self._last_update_time is not None and charger_data["online"]:
            dt_hours   = (now - self._last_update_time).total_seconds() / 3600.0
            energy_kwh = (charger_data["power_w"] * dt_hours) / 1000.0
            if energy_kwh > 0:
                self._current_session_kwh += energy_kwh
                self._month_energy_kwh    += energy_kwh
                self._total_energy_kwh    += energy_kwh
                self._save_persistent("ev_month_energy_kwh", self._month_energy_kwh)
                self._save_persistent("ev_total_energy_kwh", self._total_energy_kwh)
            if (charger_data["status"] not in CHARGER_WORKING_STATES
                    and self._session_start_time is not None):
                duration = (now - self._session_start_time).total_seconds() / 60.0
                self.log(
                    f"Sesja zakonczona: {self._current_session_kwh:.2f} kWh, "
                    f"czas: {duration:.0f} min"
                )
                self._session_start_time = None
        self._last_update_time = now
        self._last_power_w     = charger_data["power_w"]
        # Zapamiętaj stan liczników miesięcznych — posłuży za snapshot
        # "koniec miesiąca" przy najbliższym przełomie.
        self._um_snapshot = self._read_um_snapshot()

    # ------------------------------------------------------------------
    # ARCHIWUM HISTORII MIESIĘCZNEJ
    # ------------------------------------------------------------------

    def _archive_month(self, ym, ev_kwh):
        """Zapisz zamknięty miesiąc do trwałego archiwum i opublikuj sensor."""
        # Preferuj snapshot z poprzedniej iteracji; po restarcie AppDaemona
        # snapshot jest pusty — wtedy sięgnij po atrybut last_period utility_meter.
        snap = self._um_snapshot or self._read_um_last_period()
        produkcja = snap.get("produkcja")
        zuzycie   = snap.get("zuzycie")
        imp       = snap.get("import")
        eksport   = snap.get("eksport")
        samowyst  = None
        if isinstance(zuzycie, (int, float)) and zuzycie > 0 and isinstance(imp, (int, float)):
            samowyst = round((zuzycie - imp) / zuzycie * 100, 1)
        record = {
            "ym":                 ym,
            "ev_kwh":             round(ev_kwh, 2),
            "produkcja_kwh":      self._round_or_none(produkcja, 1),
            "zuzycie_kwh":        self._round_or_none(zuzycie, 1),
            "import_kwh":         self._round_or_none(imp, 1),
            "eksport_kwh":        self._round_or_none(eksport, 1),
            "samowystarczalnosc": samowyst,
        }
        history = self._load_persistent_raw("ev_history", [])
        if not isinstance(history, list):
            history = []
        # Idempotencja: nadpisz wpis dla tego miesiąca, gdyby już istniał.
        history = [h for h in history if h.get("ym") != ym]
        history.append(record)
        history.sort(key=lambda h: h.get("ym", ""))
        history = history[-HISTORY_MAX_MONTHS:]
        self._save_persistent("ev_history", history)
        self.log(f"Archiwum miesiaca {ym}: {record}")
        self._publish_history(history)

    def _read_um_snapshot(self):
        """Bieżące wartości liczników miesięcznych (stan w trakcie miesiąca)."""
        return {
            "produkcja": self._safe_float_state(SENSOR_UM_PRODUKCJA),
            "zuzycie":   self._safe_float_state(SENSOR_UM_ZUZYCIE),
            "import":    self._safe_float_state(SENSOR_UM_IMPORT),
            "eksport":   self._safe_float_state(SENSOR_UM_EKSPORT),
        }

    def _read_um_last_period(self):
        """Fallback po restarcie: utility_meter trzyma poprzedni cykl w last_period."""
        out = {}
        for key, ent in (("produkcja", SENSOR_UM_PRODUKCJA),
                         ("zuzycie",   SENSOR_UM_ZUZYCIE),
                         ("import",    SENSOR_UM_IMPORT),
                         ("eksport",   SENSOR_UM_EKSPORT)):
            try:
                lp = self.get_state(ent, attribute="last_period")
                out[key] = float(lp) if lp not in (None, "unknown", "unavailable") else None
            except Exception:
                out[key] = None
        return out

    def _publish_history(self, history=None):
        """Opublikuj sensor.ev_historia_miesieczna z całym archiwum w atrybutach."""
        if history is None:
            history = self._load_persistent_raw("ev_history", [])
            if not isinstance(history, list):
                history = []
        try:
            last_kwh = history[-1]["ev_kwh"] if history else 0
            # UWAGA: świadomie BEZ unit_of_measurement i device_class — HA 2026.x
            # odrzuca encje set_state z tymi atrybutami (patrz docs, Problem 11).
            # Archiwum (do 120 miesięcy / 10 lat) nie zmieści się w input_text (limit 255 zn.),
            # więc tu set_state jest właściwą drogą; jednostki opisują karty dashboardu.
            self.set_state(
                HISTORY_SENSOR,
                state=last_kwh,
                attributes={
                    "friendly_name": "EV Historia miesięczna",
                    "icon":          "mdi:chart-bar",
                    "months":        history,
                    "months_count":  len(history),
                },
            )
        except Exception as e:
            self.log(f"Blad publikacji historii: {e}", level="WARNING")

    def _safe_float_state(self, entity):
        try:
            v = self.get_state(entity)
            return float(v) if v not in (None, "unknown", "unavailable") else None
        except Exception:
            return None

    @staticmethod
    def _round_or_none(v, n):
        return round(v, n) if isinstance(v, (int, float)) else None

    # ------------------------------------------------------------------
    # AKTUALIZACJA HELPERÓW HA
    # ------------------------------------------------------------------

    def _update_sensors(self, charger_data, ha_data, mode, target_current):
        # streak pokazujemy dopiero od progu watchdoga (10 min) — pojedyncze
        # iteracje WORKING+0W są normalne (np. tuż po STOP), nie zaśmiecamy logu
        streak = self._working_zero_power_streak
        streak_str = (f", streak0W={streak}({streak * UPDATE_INTERVAL_S}s)"
                      if streak >= WATCHDOG_FROZEN_DP_THRESHOLD else "")
        self.log(
            f"Sensory: status={charger_data['status']}, moc={charger_data['power_w']:.0f}W, "
            f"tryb={mode}, prad_cel={target_current}A, sesja={self._current_session_kwh:.3f}kWh"
            f"{streak_str}"
        )

    def _update_ha_helpers(self, charger_data, ha_data, mode, target_current):
        try:
            emergency_remaining_min = 0
            if self._emergency_end_time and self._is_emergency_active():
                emergency_remaining_min = int(
                    (self._emergency_end_time - datetime.datetime.now()).total_seconds() / 60
                )
            data = json.dumps({
                "status":                  charger_data["status"],
                "mode":                    mode,
                "power":                   round(charger_data["power_w"], 0),
                "current":                 charger_data["current_a"],
                "target_current":          target_current,
                "session":                 round(self._current_session_kwh, 3),
                "month":                   round(self._month_energy_kwh, 3),
                "total":                   round(self._total_energy_kwh, 3),
                "surplus_w":               round(ha_data["surplus_w"], 0),
                "soc":                     ha_data["soc"],
                "emergency_remaining_min": emergency_remaining_min,
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

    # ------------------------------------------------------------------
    # PERSISTENT STORAGE
    # ------------------------------------------------------------------

    def _save_persistent(self, key, value):
        try:
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
            path = "/config/ev_charger_data.json"
            if os.path.exists(path):
                with open(path, "r") as f:
                    data = json.load(f)
                return float(data.get(key, default))
            return default
        except Exception as e:
            self.log(f"Blad odczytu persistent: {e}", level="WARNING")
            return default

    def _load_persistent_raw(self, key, default):
        """Jak _load_persistent, ale bez rzutowania na float — dla stringów/list/dictów."""
        try:
            path = "/config/ev_charger_data.json"
            if os.path.exists(path):
                with open(path, "r") as f:
                    data = json.load(f)
                return data.get(key, default)
            return default
        except Exception as e:
            self.log(f"Blad odczytu persistent: {e}", level="WARNING")
            return default
