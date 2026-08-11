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
    # Jedno źródło prawdy dla stanu runtime — ten sam kod, który wykonuje
    # initialize() na żywym HA. Bez tego testy stopniowo rozjeżdżały się
    # z produkcją przy każdym nowym polu.
    c._init_runtime_state()
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


def charger(status="SLEEP", power_w=0, online=True, metrics_raw=None,
            current_a=0, schedule=None):
    return {"status": status, "current_a": current_a, "power_w": power_w,
            "metrics": {}, "online": online, "schedule": schedule, "switch": None,
            "metrics_raw": metrics_raw}


# Realna próbka zamrożonego DP 102 z awarii 2026-08-11 (identyczna przez 36 h)
FROZEN_RAW = ('{"L1":[2430,0,0],"L2":[2430,0,0],"L3":[2430,0,0],'
              '"t":330,"p":0,"d":0,"e":0}')


def live_raw(volts=2430, amps=0, watts=0, temp=330):
    """Żywy DP 102 — realny pomiar drga między odczytami."""
    return json.dumps({"L1": [volts, amps, watts], "L2": [volts, amps, watts],
                       "L3": [volts, amps, watts], "t": temp, "p": watts,
                       "d": 0, "e": 0}, separators=(",", ":"))


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


def test_current_does_not_chatter_at_step_boundary():
    # REGRESJA (zaobserwowane 2026-07-28 13:36-13:37): 10A -> 11A -> 10A w 60 s.
    # Nadwyzka oscylowala wokol granicy stopnia 11A (7590 W), a gole int()
    # przerzucalo cel tam i z powrotem. Wallbox pikal przy kazdej zmianie.
    c = make_ctrl()
    c._last_sent_current = 10
    targets = []
    for surplus in (7600, 7550, 7620, 7580, 7610, 7595):
        t = c._smooth_current(surplus)
        targets.append(t)
        c._last_sent_current = t
    assert set(targets) == {10}, f"prad ma stac na 10A przy szumie wokol granicy: {targets}"


def test_current_rises_when_surplus_really_holds():
    # Wygladzanie nie moze zablokowac realnego wzrostu — ma go tylko opoznic
    c = make_ctrl()
    c._last_sent_current = 10
    out = []
    for _ in range(3):
        t = c._smooth_current(9000)
        out.append(t)
        c._last_sent_current = t
    assert out[0] == 10, f"pierwsza probka jeszcze nie podnosi: {out}"
    assert out[-1] > 10, f"po utrzymaniu nadwyzki prad ma wzrosnac: {out}"


def test_small_current_drop_is_damped():
    # Male redukcje wygladzamy — krotkie zejscie w magazyn jest akceptowalne
    # (decyzja Tomka 2026-07-28), a rzadsze zmiany to spokojniejszy wallbox.
    c = make_ctrl()
    c._last_sent_current = 10
    first = c._smooth_current(6300)          # ~9A, spadek o 1A
    assert first == 10, f"pojedyncza probka nie obniza: {first}"
    second = c._smooth_current(6300)
    assert second == 9, f"po potwierdzeniu obniza: {second}"


def test_large_current_drop_is_immediate():
    # Nagly duzy pobor (pompa ciepla, piekarnik) — redukcja bez czekania,
    # zeby nie przekroczyc przylacza 11 kW.
    c = make_ctrl()
    c._last_sent_current = 14
    t = c._smooth_current(4200)              # ~6A, spadek o 8A
    assert t <= 14 - ev.CURRENT_FAST_DROP_A, f"duzy spadek ma isc od razu, jest {t}"


def test_real_log_sequence_has_fewer_changes():
    # Realne nadwyzki z logow 2026-07-28 13:34-13:37 (te same, ktore dawaly
    # 5 zmian pradu w 3 minuty). Wygladzanie ma ich wyraznie ubyc.
    obs = [4757, 6600, 6670, 6803, 6993, 7710, 6907, 5097]
    naive = [max(6, min(16, int(s / 690))) for s in obs]
    naive_changes = sum(1 for a, b in zip(naive, naive[1:]) if a != b)

    c = make_ctrl()
    c._last_sent_current = 0
    smoothed = []
    for s in obs:
        t = c._smooth_current(s)
        smoothed.append(t)
        c._last_sent_current = t
    changes = sum(1 for a, b in zip(smoothed, smoothed[1:]) if a != b)

    assert changes < naive_changes, \
        f"wygladzanie ma zmniejszyc liczbe zmian: {changes} vs {naive_changes} " \
        f"(smoothed={smoothed}, naive={naive})"


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


# ── Zdrowie wallboxa: wykrywanie zawieszenia (awaria 2026-08-11) ─────────────
def _pump_health(c, iters, mode="SOLAR", raw=FROZEN_RAW, status="WORKING",
                 power_w=0):
    for _ in range(iters):
        c._update_health(charger(status, power_w=power_w, metrics_raw=raw), mode)


def test_freeze_detected_by_identical_dp102():
    # AWARIA 2026-08-11: wallbox odpowiadał w sieci, ale DP 102 stał w miejscu
    # co do bitu przez 36 h. Napiecie sieci tak nie wyglada — to sygnatura
    # zawieszenia firmware, ostrzejsza niz samo power_w == 0.
    c = make_ctrl()
    _pump_health(c, ev.FROZEN_METRICS_THRESHOLD)
    assert c._is_charger_frozen(), \
        f"po {ev.FROZEN_METRICS_THRESHOLD} identycznych odczytach ma wykryc zawieszenie"


def test_freeze_not_triggered_by_live_metrics():
    # Zywy wallbox: napiecie i temperatura drgaja miedzy odczytami.
    c = make_ctrl()
    for i in range(ev.FROZEN_METRICS_THRESHOLD * 3):
        raw = live_raw(volts=2430 + (i % 5), temp=330 + (i % 3))
        c._update_health(charger("WORKING", metrics_raw=raw), "SOLAR")
    assert not c._is_charger_frozen(), "zmieniajacy sie DP 102 to nie awaria"


def test_freeze_ignored_when_not_trying_to_charge():
    # Auto odpiete na noc — DP 102 moze stac, ale to nie jest awaria,
    # bo skrypt i tak niczego nie chce od wallboxa.
    c = make_ctrl()
    _pump_health(c, ev.FROZEN_METRICS_THRESHOLD * 2, mode="BATTERY_PRIORITY",
                 status="SLEEP")
    assert not c._is_charger_frozen(), "postoj bez checi ladowania to nie awaria"


def test_freeze_survives_mode_flapping():
    # REGRESJA 2026-08-11 12:27: nadwyzka migotala wokol progu, tryb skakal
    # SOLAR <-> IDLE i zerowal licznik przy stanie 40. Watchdog przez to
    # potrafil nie uderzyc ani razu przez cala awarie.
    c = make_ctrl()
    for i in range(ev.FROZEN_METRICS_THRESHOLD * 2):
        mode = "SOLAR" if i % 3 else "IDLE"        # co trzecia iteracja IDLE
        c._update_health(charger("WORKING", metrics_raw=FROZEN_RAW), mode)
    assert c._is_charger_frozen(), \
        "migotanie trybu nie moze kasowac wykrycia zawieszenia"


def test_freeze_notifies_once_and_dismisses_on_recovery():
    # 36 h ciszy bylo glownym kosztem tej awarii — ma powstac powiadomienie
    # w HA, dokladnie jedno na epizod, i ma zniknac po powrocie do pracy.
    c = make_ctrl()
    _pump_health(c, ev.FROZEN_METRICS_THRESHOLD + 5)
    creates = [s for s, _ in c.service_calls if s == "persistent_notification/create"]
    assert len(creates) == 1, f"oczekiwano 1 powiadomienia, jest {len(creates)}"

    # Reboot pomogl: DP 102 rusza, moc rosnie
    for i in range(3):
        c._update_health(charger("WORKING", power_w=3700,
                                 metrics_raw=live_raw(2430 + i, amps=60,
                                                      watts=37)), "SOLAR")
    dismisses = [s for s, _ in c.service_calls
                 if s == "persistent_notification/dismiss"]
    assert len(dismisses) == 1, "po powrocie do pracy powiadomienie ma zniknac"


def test_unresponsive_commands_escalate():
    # AWARIA 2026-08-11: 4 x START bez efektu (status IDLE) wczoraj i 4 x STOP
    # bez efektu (status WORKING) dzis. Osobne liczniki nie laczyly kropek.
    c = make_ctrl()
    for _ in range(60):
        c._apply_decision("BATTERY_PRIORITY", 0, charger("WORKING", power_w=4140))
    assert c._unresponsive_cmds >= ev.UNRESPONSIVE_CMD_THRESHOLD, \
        f"komendy bez efektu maja sie zliczac, jest {c._unresponsive_cmds}"
    c._update_health(charger("WORKING", metrics_raw=live_raw()), "BATTERY_PRIORITY")
    creates = [s for s, _ in c.service_calls if s == "persistent_notification/create"]
    assert creates, "wallbox ignorujacy komendy ma dac powiadomienie"


def test_unresponsive_counter_resets_when_command_works():
    c = make_ctrl()
    for _ in range(20):
        c._apply_decision("BATTERY_PRIORITY", 0, charger("WORKING", power_w=4140))
    assert c._unresponsive_cmds > 0
    c._apply_decision("BATTERY_PRIORITY", 0, charger("PAUSE"))   # STOP zadzialal
    assert c._unresponsive_cmds == 0, "skuteczna komenda zeruje licznik"


def test_zero_power_streak_survives_mode_flapping():
    # Ten sam blad migotania dotykal fallbacku kompensacji: przy zerowanym
    # streaku skrypt przestawal podstawiac ostatnia znana moc i widzial
    # wielki deficyt, wiec STOPowal realnie trwajaca sesje.
    c = make_ctrl()
    c._last_nonzero_power_w = 7590.0
    for i in range(6):
        mode = "IDLE" if i == 3 else "SOLAR"
        c._update_diag(charger("WORKING", power_w=0), mode)
    assert c._working_zero_power_streak >= ev.FROZEN_DP_FALLBACK_ITERS, \
        f"streak ma przezyc chwilowe IDLE, jest {c._working_zero_power_streak}"


# ── Ponawianie startu i budzenie sesji ───────────────────────────────────────
def test_start_retries_resume_after_cooldown():
    # AWARIA 2026-08-10: o 10:30 skrypt zapisal "odpuszczam do zmiany warunkow"
    # i przy nadwyzce 8.6 kW nie sprobowal juz ANI RAZU do konca dnia. Reset
    # licznika byl mozliwy tylko w galezi nieaktywnego trybu, a przy trwalej
    # nadwyzce ta galaz nigdy nie jest osiagana.
    c = make_ctrl()
    for _ in range(30):
        c._apply_decision("SOLAR", 6, charger("SLEEP"))
    sends_before = len(c._device.switch_sends())
    assert sends_before == 1 + ev.SWITCH_MAX_START_RETRIES, \
        f"najpierw seria prob, jest {sends_before}"

    for _ in range(ev.START_RETRY_COOLDOWN_ITERS + ev.SWITCH_RETRY_ITERATIONS + 2):
        c._apply_decision("SOLAR", 6, charger("SLEEP"))
    assert len(c._device.switch_sends()) > sends_before, \
        "po cooldownie skrypt ma sprobowac ponownie, a nie milczec do konca dnia"


def test_start_retries_reset_on_status_change():
    # Zmiana statusu ladowarki to zmiana warunkow (przepiety kabel, wallbox
    # wrocil po reboocie) — liczniki prob maja ruszyc od nowa.
    c = make_ctrl()
    for _ in range(30):
        c._apply_decision("SOLAR", 6, charger("SLEEP"))
    sends_before = len(c._device.switch_sends())
    for _ in range(ev.SWITCH_RETRY_ITERATIONS + 1):
        c._apply_decision("SOLAR", 6, charger("IDLE"))
    assert len(c._device.switch_sends()) > sends_before, \
        "po zmianie statusu ma wrocic do ponawiania"


def test_wake_cycle_when_car_stops_drawing():
    # Wallbox trzyma WORKING, ale auto nie pobiera (zasnelo po STOPie —
    # Stellantis nie wznawia sesji sam). Bez cyklu STOP/START skrypt nie ma
    # zadnej sciezki wznowienia: warunek `not charger_working` nigdy nie jest
    # prawdziwy, wiec START nie idzie (2026-08-11, 36 h martwego stanu).
    c = make_ctrl()
    c._last_sent_switch = True
    c._working_zero_power_streak = ev.WAKE_CYCLE_AFTER_ITERS
    c._apply_decision("SOLAR", 6, charger("WORKING", power_w=0))
    sends = c._device.switch_sends()
    assert sends and sends[-1][1] is False, \
        f"cykl budzenia zaczyna sie od STOP, jest {sends}"


def test_wake_cycle_is_limited():
    # Problem 13 (maj 2026): kazdy STOP to cykl stycznika, slyszalny przez okno.
    # Budzenie musi miec twardy limit, nie moze przejsc w wieczne klikanie.
    c = make_ctrl()
    c._last_sent_switch = True
    for _ in range(200):
        c._working_zero_power_streak = ev.WAKE_CYCLE_AFTER_ITERS
        c._apply_decision("SOLAR", 6, charger("WORKING", power_w=0))
    stops = [v for _, v in c._device.switch_sends() if v is False]
    assert len(stops) <= ev.WAKE_CYCLE_MAX_ATTEMPTS, \
        f"budzenie ma miec twardy limit, jest {len(stops)} STOPow"


def test_wake_cycle_not_triggered_too_early():
    # Auto negocjuje z wallboxem ~minute po starcie — 0 W przez chwile
    # to norma, nie powod do szarpania stycznikiem.
    c = make_ctrl()
    c._last_sent_switch = True
    c._working_zero_power_streak = ev.WAKE_CYCLE_AFTER_ITERS - 1
    c._apply_decision("SOLAR", 6, charger("WORKING", power_w=0))
    assert not c._device.switch_sends(), "przed progiem zadnych komend switch"


def test_wake_attempts_reset_when_power_returns():
    c = make_ctrl()
    c._wake_attempts = 1
    c._apply_decision("SOLAR", 6, charger("WORKING", power_w=4140))
    assert c._wake_attempts == 0, "realny pobor zeruje licznik budzen"


# ── Sprzezenie zwrotne: prad i harmonogram ───────────────────────────────────
def _current_sends(c):
    return [v for dp, v in c._device.calls if dp == str(ev.DP_CURRENT)]


def test_current_mismatch_is_detected_and_retried():
    # AWARIA 2026-08-11: skrypt wyslal 6A, 9A, 7A, 8A do zawieszonego wallboxa
    # i kazda uznal za sukces, bo set_value nie rzucil wyjatkiem. DP 150 byl
    # czytany, ale nigdy porownywany z tym, co wyslalismy.
    c = make_ctrl()
    c._last_sent_switch = True
    stuck = charger("WORKING", power_w=4140, current_a=6)   # wallbox stoi na 6A
    c._apply_decision("SOLAR", 9, stuck)
    for _ in range(ev.CURRENT_VERIFY_ITERS + 1):
        c._apply_decision("SOLAR", 9, stuck)
    assert len(_current_sends(c)) >= 2, \
        f"niepotwierdzony prad ma byc ponowiony, wyslano {_current_sends(c)}"


def test_confirmed_current_is_not_resent():
    # Odwrotna strona: gdy wallbox potwierdzil zadany prad, nie ma powodu
    # do ponowien — to byloby pikanie wallboxa z Problemu 23.
    c = make_ctrl()
    c._last_sent_switch = True
    c._apply_decision("SOLAR", 9, charger("WORKING", power_w=4140, current_a=6))
    for _ in range(10):
        c._apply_decision("SOLAR", 9, charger("WORKING", power_w=4140, current_a=9))
    assert len(_current_sends(c)) == 1, \
        f"potwierdzony prad nie wymaga ponowien: {_current_sends(c)}"


def test_current_not_verified_when_charger_idle():
    # Stojacy wallbox moze raportowac inny prad niz ostatnio zadany — to nie
    # jest awaria i nie moze generowac ponowien co minute (Problem 23).
    c = make_ctrl()
    for _ in range(10):
        c._apply_decision("SOLAR", 9, charger("SLEEP", current_a=0))
    assert len(_current_sends(c)) == 1, \
        f"przy stojacym wallboxie bez ponowien pradu: {_current_sends(c)}"


def test_cloud_pushed_schedule_is_cleared():
    # 2026-08-11 13:09, tuz po reboocie wallboxa: chmura Tuya wepchnela
    # {"ss":"15:00","se":"17:00"}. Skrypt czyscil DP 151 tylko w initialize()
    # i _send_start(), wiec przy wallboxie w WORKING harmonogram tam zostawal.
    c = make_ctrl()
    pushed = '{"m":0,"dt":0,"ss":"15:00","se":"17:00"}'
    c._update_diag(charger("WORKING", power_w=4140, schedule=pushed), "SOLAR")
    assert [v for dp, v in c._device.calls if dp == "151"], \
        "wepchniety harmonogram ma byc od razu wyczyszczony"


def test_empty_schedule_is_not_touched():
    c = make_ctrl()
    empty = '{"m":0,"dt":0,"ss":"00:00","se":"00:00"}'
    c._update_diag(charger("WORKING", power_w=4140, schedule=empty), "SOLAR")
    assert not [v for dp, v in c._device.calls if dp == "151"], \
        "pusty harmonogram nie wymaga czyszczenia"


def test_schedule_clearing_has_a_limit():
    # Gdyby chmura wpychala harmonogram w kolko, nie wolno wpasc w ping-pong
    # komend co 30 s.
    c = make_ctrl()
    for i in range(40):
        pushed = '{"m":0,"dt":0,"ss":"1%d:00","se":"17:00"}' % (i % 10)
        c._update_diag(charger("WORKING", power_w=4140, schedule=pushed), "SOLAR")
    clears = [v for dp, v in c._device.calls if dp == "151"]
    assert len(clears) <= ev.SCHEDULE_CLEAR_MAX, \
        f"czyszczenie harmonogramu ma miec limit, jest {len(clears)}"


def test_awaria_2026_08_11_wykryta_w_kilka_minut():
    # Pelny scenariusz awarii: wallbox zamrozony (DP 102 identyczny co do bitu),
    # nadwyzka migocze wokol progu wiec tryb skacze SOLAR/IDLE, status stoi na
    # WORKING. Realnie trwalo to 36 godzin w calkowitej ciszy.
    c = make_ctrl()
    wykryto_po = None
    for i in range(30):
        mode = "SOLAR" if i % 4 else "IDLE"
        c._update_health(charger("WORKING", metrics_raw=FROZEN_RAW), mode)
        if [s for s, _ in c.service_calls if s == "persistent_notification/create"]:
            wykryto_po = (i + 1) * ev.UPDATE_INTERVAL_S / 60.0
            break
    assert wykryto_po is not None, "awaria musi zostac wykryta"
    assert wykryto_po <= 10, f"oczekiwano wykrycia w ~5 min, jest {wykryto_po} min"


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
