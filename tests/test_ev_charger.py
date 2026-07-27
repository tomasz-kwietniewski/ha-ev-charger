# -*- coding: utf-8 -*-
"""Lekkie testy jednostkowe ev_charger.py — bez frameworka, bez zależności.

Uruchomienie:  python tests/test_ev_charger.py
Stubujemy appdaemon/tinytuya/requests w sys.modules, a ścieżki secrets
i persystencji podmieniamy przez EV_SECRETS_PATH / EV_DATA_PATH.
"""
import json
import os
import sys
import tempfile
import types

# ── Środowisko: temp katalog na secrets i dane persystentne ──────────────────
TMP = tempfile.mkdtemp(prefix="ev_charger_test_")
_secrets_path = os.path.join(TMP, "secrets.json")
with open(_secrets_path, "w") as f:
    json.dump({"device_id": "test", "device_ip": "127.0.0.1", "device_key": "k"}, f)
os.environ["EV_SECRETS_PATH"] = _secrets_path
DATA_PATH = os.path.join(TMP, "ev_charger_data.json")
os.environ["EV_DATA_PATH"] = DATA_PATH


# ── Stub AppDaemon Hass ──────────────────────────────────────────────────────
class FakeHass:
    def __init__(self, *args, **kwargs):
        self.states = {}
        self.service_calls = []
        self.logs = []

    def log(self, msg, level="INFO"):
        self.logs.append((level, str(msg)))

    def get_state(self, entity, attribute=None):
        if attribute is not None:
            return self.states.get((entity, attribute))
        return self.states.get(entity)

    def call_service(self, service, **kwargs):
        self.service_calls.append((service, kwargs))

    def listen_state(self, cb, entity, **kwargs):
        pass

    def run_every(self, cb, start, interval, **kwargs):
        pass


_mod_ad  = types.ModuleType("appdaemon")
_mod_pl  = types.ModuleType("appdaemon.plugins")
_mod_hs  = types.ModuleType("appdaemon.plugins.hass")
_mod_api = types.ModuleType("appdaemon.plugins.hass.hassapi")
_mod_api.Hass = FakeHass
_mod_ad.plugins = _mod_pl
_mod_pl.hass = _mod_hs
_mod_hs.hassapi = _mod_api
sys.modules.update({
    "appdaemon": _mod_ad,
    "appdaemon.plugins": _mod_pl,
    "appdaemon.plugins.hass": _mod_hs,
    "appdaemon.plugins.hass.hassapi": _mod_api,
})

_mod_tt = types.ModuleType("tinytuya")


class _StubTTDevice:
    def __init__(self, *a, **k):
        pass

    def set_socketTimeout(self, *a):
        pass

    def set_socketRetryLimit(self, *a):
        pass

    def status(self):
        return {"dps": {}}

    def set_value(self, *a):
        pass


_mod_tt.Device = _StubTTDevice
sys.modules["tinytuya"] = _mod_tt

_mod_rq = types.ModuleType("requests")
_mod_rq.post = lambda *a, **k: types.SimpleNamespace(status_code=201, text="")
sys.modules["requests"] = _mod_rq

# ── Import testowanego modułu ────────────────────────────────────────────────
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "..", "appdaemon", "apps"))
import ev_charger as ev  # noqa: E402


# ── Pomocnicze ───────────────────────────────────────────────────────────────
class FakeDevice:
    """Sterowalna atrapa tinytuya.Device do testów _apply_decision/_get_charger_data."""

    def __init__(self):
        self.fail_switch = False
        self.fail_all = False
        self.calls = []
        self.status_response = {"dps": {"109": "sleep"}}

    def status(self):
        return self.status_response

    def set_value(self, dp, value):
        self.calls.append((str(dp), value))
        if self.fail_all:
            raise OSError("network down")
        if self.fail_switch and str(dp) == str(ev.DP_SWITCH):
            raise OSError("network down")

    def switch_sends(self):
        return [c for c in self.calls if c[0] == str(ev.DP_SWITCH)]


def make_ctrl(states=None):
    c = ev.EVChargerControl()
    c.states = dict(states or {})
    c._surplus_history = []
    c._last_sent_current = -1
    c._last_sent_switch = None
    c._switch_mismatch_iters = 0
    c._start_retries = 0
    c._stop_retries = 0
    c._charger_active = False
    c._working_zero_power_streak = 0
    c._last_nonzero_power_w = 0.0
    c._session_start_time = None
    c._current_session_kwh = 0.0
    c._emergency_end_time = None
    c._device_error_count = 0
    c._device = FakeDevice()
    return c


def ha_data(surplus_w=0, soc=100.0, price=1.0):
    return {"soc": soc, "pv_power": 0.0, "load_power": 0.0, "grid_power": 0.0,
            "avg_available_kw": surplus_w / 1000.0, "surplus_w": surplus_w,
            "price": price}


def set_env(c, pv, load, pcc, soc=100.0, price=1.0):
    """Ustaw sensory HA. load to CAŁE zużycie domu, razem z ładowarką."""
    c.states.update({
        ev.SENSOR_SOC: str(soc), ev.SENSOR_PV_POWER: str(pv),
        ev.SENSOR_LOAD_POWER: str(load), ev.SENSOR_GRID_POWER: str(pcc),
        ev.SENSOR_PRICE: str(price),
    })


def charger(status="SLEEP", power_w=0, online=True):
    return {"status": status, "current_a": 0, "power_w": power_w,
            "metrics": {}, "online": online, "schedule": None, "switch": None,
            "metrics_raw": None}


# ── Testy ────────────────────────────────────────────────────────────────────
def test_surplus_to_current():
    c = make_ctrl()
    assert c._surplus_to_current(690) == 6, "ponizej minimum -> clamp do 6A"
    assert c._surplus_to_current(5000) == 7, "5000/690 = 7.2 -> 7A"
    assert c._surplus_to_current(50000) == 16, "clamp do 16A"


def test_surplus_deficit_is_negative():
    # K1: import 2 kW nie może być maskowany floorem +1000 W
    c = make_ctrl({
        ev.SENSOR_SOC: "100", ev.SENSOR_PV_POWER: "1.0",
        ev.SENSOR_LOAD_POWER: "3.0", ev.SENSOR_GRID_POWER: "-2.0",
        ev.SENSOR_PRICE: "1.0",
    })
    data = c._get_ha_data(charger("SLEEP"))
    assert data["surplus_w"] == -1000, f"oczekiwano -1000, jest {data['surplus_w']}"


def test_surplus_sees_battery_masking():
    # K1: falownik trzyma PCC~0 rozladowujac magazyn — PV-dom widzi deficyt
    c = make_ctrl({
        ev.SENSOR_SOC: "100", ev.SENSOR_PV_POWER: "1.0",
        ev.SENSOR_LOAD_POWER: "5.0", ev.SENSOR_GRID_POWER: "0.0",
        ev.SENSOR_PRICE: "1.0",
    })
    data = c._get_ha_data(charger("SLEEP"))
    assert data["surplus_w"] == -3000, f"oczekiwano -3000, jest {data['surplus_w']}"


def test_surplus_battery_charging_uses_pcc():
    # Magazyn dobija sie (PCC < PV-dom): min() nie podbiera mocy baterii
    c = make_ctrl({
        ev.SENSOR_SOC: "96", ev.SENSOR_PV_POWER: "6.0",
        ev.SENSOR_LOAD_POWER: "1.0", ev.SENSOR_GRID_POWER: "2.0",
        ev.SENSOR_PRICE: "1.0",
    })
    data = c._get_ha_data(charger("SLEEP"))
    assert data["surplus_w"] == 3000, f"oczekiwano 3000 (PCC 2kW + bias), jest {data['surplus_w']}"


def test_surplus_stable_across_session_start():
    # Kompensacja poboru ladowarki musi isc PRZED usrednianiem — inaczej
    # srednia miesza probki z rozna moca ladowania i prad skacze po starcie.
    c = make_ctrl()
    # t1: auto nie laduje, PV 8 kW, dom 1 kW -> dostepne 7 kW
    set_env(c, pv=8.0, load=1.0, pcc=7.0)
    d1 = c._get_ha_data(charger("SLEEP"))
    # t2: auto laduje 7.59 kW (11A) -> dom 8.59 kW, PCC -0.59; dostepne dalej 7 kW
    set_env(c, pv=8.0, load=8.59, pcc=-0.59)
    d2 = c._get_ha_data(charger("WORKING", power_w=7590))
    assert abs(d1["surplus_w"] - d2["surplus_w"]) < 50, \
        f"nadwyzka skoczyla po starcie sesji: {d1['surplus_w']:.0f} -> {d2['surplus_w']:.0f}"
    assert c._surplus_to_current(d2["surplus_w"]) == c._surplus_to_current(d1["surplus_w"])


def test_solar_regulation_converges_under_cloud():
    # Petla domkniecia: zachmurzenie ma redukowac prad, nie podkrecac go.
    c = make_ctrl()
    current_a = 11
    targets = []
    for _ in range(6):
        ev_kw = current_a * 3 * 230 / 1000.0
        pv, base = 1.0, 1.5                       # gesta chmura
        load = base + ev_kw
        pcc = max(pv - load, -0.5)                # magazyn maskuje deficyt
        set_env(c, pv=pv, load=load, pcc=pcc, soc=99.0)
        cd = charger("WORKING", power_w=int(ev_kw * 1000))
        mode, target = c._decide(c._get_ha_data(cd), cd)
        targets.append((mode, target))
        if mode == "SOLAR":
            current_a = target
        else:
            current_a = 0
    assert targets[-1][0] == "IDLE", f"po 6 iteracjach chmury oczekiwano IDLE, jest {targets}"
    solar_targets = [t for m, t in targets if m == "SOLAR"]
    assert solar_targets == sorted(solar_targets, reverse=True), \
        f"prad ma maleć, nie rosnąć: {solar_targets}"


def test_frozen_dp102_does_not_fake_deficit():
    # Zamrozony DP 102 (WORKING + 0W) nie moze wygladac jak wielki deficyt —
    # inaczej skrypt STOPuje realnie trwajaca sesje zamiast dac ostrzec watchdogowi.
    c = make_ctrl()
    c._last_nonzero_power_w = 7590.0
    c._working_zero_power_streak = ev.FROZEN_DP_FALLBACK_ITERS
    set_env(c, pv=8.0, load=8.59, pcc=-0.59)          # auto realnie bierze 7.59 kW
    frozen = charger("WORKING", power_w=0)             # ale pomiar pokazuje 0
    data = c._get_ha_data(frozen)
    mode, _ = c._decide(data, frozen)
    assert mode == "SOLAR", f"oczekiwano SOLAR (bez STOPu), jest {mode}"


def test_frozen_dp102_fallback_needs_streak():
    # Chwilowe WORKING+0W tuz po starcie (negocjacja auta) nie uruchamia fallbacku
    c = make_ctrl()
    c._last_nonzero_power_w = 7590.0
    c._working_zero_power_streak = 0
    set_env(c, pv=8.0, load=1.0, pcc=7.0)
    data = c._get_ha_data(charger("WORKING", power_w=0))
    assert data["surplus_w"] == 8000, f"bez streaka brak kompensacji, jest {data['surplus_w']}"


def test_decide_solar_stops_below_hysteresis():
    # surplus_w to juz pelna dostepna moc (z kompensacja ladowarki) — ponizej STOP -> IDLE
    c = make_ctrl()
    mode, cur = c._decide(ha_data(surplus_w=1140), charger("WORKING", power_w=4140))
    assert (mode, cur) == ("IDLE", 0), f"oczekiwano IDLE, jest {(mode, cur)}"


def test_decide_solar_reduces_to_min_on_deficit():
    # Dostepne 3140 W przy ladowaniu 4140 W -> zejdz do 6A, nie podkrecaj
    c = make_ctrl()
    mode, cur = c._decide(ha_data(surplus_w=3140), charger("WORKING", power_w=4140))
    assert (mode, cur) == ("SOLAR", 6), f"oczekiwano SOLAR 6A, jest {(mode, cur)}"


def test_decide_no_start_below_threshold():
    c = make_ctrl()
    mode, cur = c._decide(ha_data(surplus_w=1500), charger("SLEEP"))
    assert (mode, cur) == ("IDLE", 0)


def test_decide_starts_above_threshold():
    c = make_ctrl()
    mode, cur = c._decide(ha_data(surplus_w=5000), charger("SLEEP"))
    assert (mode, cur) == ("SOLAR", 7)


def test_decide_negative_price_uses_buffered_current():
    # S2: przy ujemnej cenie nie bierzemy pelnych 16A (11 kW = cale przylacze)
    c = make_ctrl()
    mode, cur = c._decide(ha_data(surplus_w=0, price=-0.5), charger("SLEEP"))
    assert (mode, cur) == ("NEGATIVE_PRICE", ev.NEGATIVE_PRICE_CURRENT_A)
    assert cur < ev.MAX_CURRENT_A


def test_decide_battery_priority():
    c = make_ctrl()
    mode, cur = c._decide(ha_data(surplus_w=5000, soc=80.0), charger("SLEEP"))
    assert (mode, cur) == ("BATTERY_PRIORITY", 0)


def test_apply_start_retries_failed_send():
    # K2: nieudana wysylka START nie moze blokowac na stale
    c = make_ctrl()
    c._device.fail_switch = True
    c._apply_decision("SOLAR", 6, charger("SLEEP"))
    assert c._last_sent_switch is None, "porazka wysylki nie moze ustawic _last_sent_switch"
    c._apply_decision("SOLAR", 6, charger("SLEEP"))
    assert len(c._device.switch_sends()) == 2, "retry co iteracje przy nieudanej wysylce"
    c._device.fail_switch = False
    c._apply_decision("SOLAR", 6, charger("SLEEP"))
    assert c._last_sent_switch is True
    assert c._charger_active is True


def test_apply_start_retries_no_effect_then_gives_up():
    # K2: START wyslany OK, ale wallbox nie laduje -> max 3 ponowienia co 4 iteracje
    c = make_ctrl()
    for _ in range(30):
        c._apply_decision("SOLAR", 6, charger("SLEEP"))
    sends = len(c._device.switch_sends())
    assert sends == 1 + ev.SWITCH_MAX_START_RETRIES, \
        f"oczekiwano {1 + ev.SWITCH_MAX_START_RETRIES} wysylek START, jest {sends}"


def test_apply_start_counters_reset_when_working():
    c = make_ctrl()
    c._apply_decision("SOLAR", 6, charger("SLEEP"))
    for _ in range(3):
        c._apply_decision("SOLAR", 6, charger("SLEEP"))
    c._apply_decision("SOLAR", 6, charger("WORKING", power_w=4140))
    assert c._switch_mismatch_iters == 0
    assert c._start_retries == 0


def test_apply_stop_retries_then_stops_clicking():
    # K2 + Problem 13: STOP ponawiany, ale nie w nieskonczonosc (cykle stycznika)
    c = make_ctrl()
    for _ in range(60):
        c._apply_decision("BATTERY_PRIORITY", 0, charger("WORKING", power_w=4140))
    sends = [v for _, v in c._device.switch_sends()]
    assert all(v is False for v in sends)
    assert len(sends) == 1 + ev.SWITCH_MAX_STOP_RETRIES, \
        f"oczekiwano {1 + ev.SWITCH_MAX_STOP_RETRIES} STOPow, jest {len(sends)}"
    assert any(lvl == "ERROR" for lvl, _ in c.logs), "brak eskalacji do ERROR"


def test_apply_start_immediate_after_idle_gap():
    # Po przerwie IDLE (ladowarka stoi) START ma isc od razu, bez cyklu ponowien
    c = make_ctrl()
    c._apply_decision("SOLAR", 6, charger("SLEEP"))
    assert len(c._device.switch_sends()) == 1
    c._apply_decision("IDLE", 0, charger("SLEEP"))       # nadwyzka znikla
    c._apply_decision("SOLAR", 6, charger("SLEEP"))      # i wrocila
    assert len(c._device.switch_sends()) == 2, "START po powrocie nadwyzki ma isc od razu"


def test_apply_stop_failed_send_retries_next_iteration():
    c = make_ctrl()
    c._device.fail_switch = True
    c._apply_decision("BATTERY_PRIORITY", 0, charger("WORKING", power_w=4140))
    assert c._last_sent_switch is None
    c._device.fail_switch = False
    c._apply_decision("BATTERY_PRIORITY", 0, charger("WORKING", power_w=4140))
    assert c._last_sent_switch is False


def test_charger_data_detects_error_dict():
    # S1: tinytuya zwraca {"Error": ...} bez wyjatku -> offline, nie "UNKNOWN=gotowy"
    c = make_ctrl()
    c._device.status_response = {"Error": "Network Error", "Err": "901"}
    data = c._get_charger_data()
    assert data["online"] is False
    assert data["status"] == "offline"


def test_charger_data_parses_working():
    c = make_ctrl()
    metrics = json.dumps({"L1": [230, 10, 8], "L2": [230, 10, 8],
                          "L3": [230, 10, 8], "e": 5, "t": 360})
    c._device.status_response = {"dps": {"109": "working", "150": 8, "102": metrics}}
    data = c._get_charger_data()
    assert data["online"] is True
    assert data["status"] == "WORKING"
    assert data["power_w"] == 2400, f"(8+8+8)*100, jest {data['power_w']}"


def test_persistent_atomic_and_corrupt_recovery():
    # S3: uszkodzony JSON -> kopia .corrupt + start od pustego; zapis atomowy
    for p in (DATA_PATH, DATA_PATH + ".corrupt", DATA_PATH + ".tmp"):
        if os.path.exists(p):
            os.remove(p)
    with open(DATA_PATH, "w") as f:
        f.write('{"ev_total_energy_kwh": 12.5, CORRUPT')
    c = make_ctrl()
    assert c._load_persistent("ev_total_energy_kwh", 5.0) == 5.0
    assert os.path.exists(DATA_PATH + ".corrupt"), "uszkodzony plik ma byc odlozony"
    c._save_persistent_many({"ev_month_energy_kwh": 1.5, "ev_total_energy_kwh": 99.9})
    assert c._load_persistent("ev_total_energy_kwh", 0.0) == 99.9
    assert c._load_persistent("ev_month_energy_kwh", 0.0) == 1.5
    assert not os.path.exists(DATA_PATH + ".tmp"), "plik tmp ma zniknac po os.replace"


def test_input_text_payload_fits_255():
    # D4: najgorszy realny przypadek musi miescic sie w limicie input_text
    c = make_ctrl({ev.SENSOR_SOC: "100"})
    c._current_session_kwh = 99.999
    c._month_energy_kwh = 9999.999
    c._total_energy_kwh = 99999.999
    captured = {}

    def capture(service, **kwargs):
        captured[kwargs.get("entity_id")] = kwargs.get("value")

    c.call_service = capture
    c._update_ha_helpers(charger("WORKING", power_w=11040),
                         ha_data(surplus_w=-11040, soc=100.0),
                         "BATTERY_PRIORITY", 16)
    payload = captured["input_text.ev_data"]
    assert len(payload) <= 255, f"payload {len(payload)} znakow: {payload}"


# ── Runner ───────────────────────────────────────────────────────────────────
def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} testow OK")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
