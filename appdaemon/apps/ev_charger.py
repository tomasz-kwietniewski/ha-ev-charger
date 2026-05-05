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
WINTER_MAX_CURRENT  = 10     # [A] max prad zima (bezpieczny limit przy 11kW przylaczu)
WINTER_START_HOUR   = 22     # godzina startu nocnego ladowania
WINTER_END_HOUR     = 6      # godzina konca nocnego ladowania

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

        # Sprawdz tryb zimowy
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
            import os
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
            import os
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
