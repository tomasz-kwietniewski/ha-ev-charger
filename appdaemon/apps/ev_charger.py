import appdaemon.plugins.hass.hassapi as hass
import tinytuya
import requests
import json
import datetime
import os
import time

# Dane urządzenia ładowarki — czytane z osobnego pliku secrets.
# UWAGA na mapowanie ścieżek AppDaemon: w środowisku add-onu "/config/"
# mapuje się na katalog add-onu (/addon_configs/a0d7b954_appdaemon/),
# NIE na główny katalog HA (/config/). Aktywny plik sekretów to:
#   /addon_configs/a0d7b954_appdaemon/ev_charger_secrets.json
# Zmienne środowiskowe EV_SECRETS_PATH / EV_DATA_PATH pozwalają podmienić
# ścieżki w testach jednostkowych (patrz tests/).
_SECRETS_PATH = os.environ.get("EV_SECRETS_PATH", "/config/ev_charger_secrets.json")
_PERSIST_PATH = os.environ.get("EV_DATA_PATH", "/config/ev_charger_data.json")
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
# Ujemna cena: też zostaw bufor na dom — 16A = 11kW zjadłoby całe przyłącze.
NEGATIVE_PRICE_CURRENT_A = 13
PHASES              = 3
VOLTAGE             = 230

# --- Progi nadwyżki solarnej (z uwzględnieniem SURPLUS_BIAS_W) ---
START_SURPLUS_W = 1600   # [W] min nadwyżka (po doliczeniu biasu) do startu
STOP_SURPLUS_W  = 1200   # [W] poniżej - zatrzymaj ładowanie (histereza)

# Bufor zachęcający do startu: doliczany do realnej nadwyżki.
# Dzięki temu auto startuje już przy ~0.6 kW realnego eksportu (1.6 - 1.0)
# zamiast czekać na pełne 1.6 kW. Przy imporcie surplus_w jest UJEMNY
# (plus bias) — bez floora, żeby regulacja widziała wielkość deficytu.
SURPLUS_BIAS_W = 1000

# --- Uśrednianie nadwyżki (wygładzanie migotania PCC) ---
PCC_HISTORY_SIZE = 3     # ile ostatnich odczytów uśredniać (3 * 30s = 90s)

# --- Wygładzanie zmian prądu (ograniczenie "pikania" wallboxa) ---
# Bez strefy nieczułości sterownik gonił szum: przy nadwyżce oscylującej wokół
# granicy stopnia (1 A = 690 W) int() przerzucał cel tam i z powrotem —
# zaobserwowane 2026-07-28: 10A -> 11A -> 10A w ciągu 60 sekund.
# Drugi powód: auto dochodzi do zadanego prądu z opóźnieniem ~1 min, a jego
# pobór wraca do wyliczenia nadwyżki. Pętla szybsza niż obiekt, którym steruje,
# sama generuje oscylacje — dlatego zmianę trzeba potwierdzić przed wysłaniem.
CURRENT_STEP_MARGIN_W = 250   # [W] histereza wokół progu stopnia (w obie strony)
CURRENT_HOLD_ITERS    = 2     # ile iteracji nowy cel musi się utrzymać (2 x 30s)
# Duży spadek idzie natychmiast — chroni przyłącze 11 kW, gdy nagle ruszy
# pompa ciepła albo piekarnik. Małe redukcje wygładzamy: krótkie zejście
# w magazyn domowy jest akceptowalne (decyzja Tomka, 2026-07-28), a rzadsze
# zmiany oznaczają spokojniejszą ładowarkę.
CURRENT_FAST_DROP_A   = 3     # [A] spadek o tyle lub więcej -> bez czekania
# Weryfikacja sprzężenia zwrotnego: DP 150 mówi, jaki prąd wallbox faktycznie
# ma ustawiony. Bez porównania z tym, co wysłaliśmy, komenda do zawieszonego
# urządzenia wygląda jak sukces (set_value nie rzuca wyjątkiem). 2 iteracje
# tolerancji, bo wallbox raportuje nową wartość z opóźnieniem.
CURRENT_VERIFY_ITERS  = 2

# Ile razy czyścić harmonogram DP 151 wpychany przez chmurę Tuya, zanim
# odpuścimy. Limit chroni przed ping-pongiem komend co 30 s, gdyby chmura
# uparcie wracała ze swoim ustawieniem.
SCHEDULE_CLEAR_MAX    = 5
# Po tylu iteracjach z czystym harmonogramem uznajemy sprawę za załatwioną
# i licznik czyszczeń rusza od zera — inaczej jedno wpychanie dziennie
# wyczerpałoby limit po kilku dniach pracy AppDaemona.
SCHEDULE_CLEAR_RESET_ITERS = 20

# --- Ponowienia komendy switch (START/STOP) ---
# Dedup po _last_sent_switch chroni przed spamem (Problem 13), ale bez
# ponowień jedna zgubiona/nieskuteczna komenda blokowała sterowanie na stałe.
SWITCH_RETRY_ITERATIONS  = 4   # co ile iteracji ponawiać przy niezgodności (4 x 30s = 2 min)
SWITCH_MAX_START_RETRIES = 3   # ile razy ponawiać START zanim odpuścimy (auto może być pełne)
# STOP ponawiamy dłużej (ochrona magazynu), ale też z limitem — każdy STOP to
# cykl stycznika wallboxa, a wieczne klikanie było oryginalnym Problemem 13.
SWITCH_MAX_STOP_RETRIES  = 5   # 5 x 2 min = 10 minut prób, potem głośny ERROR
# Odpuszczenie prób STARTu nie może być wieczne. 2026-08-10: po trzech
# nieudanych próbach skrypt zamilkł na resztę dnia, choć nadwyżka sięgała
# 8,6 kW. Reset licznika był osiągalny wyłącznie przez gałąź nieaktywnego
# trybu, w którą przy trwałej nadwyżce w ogóle się nie wchodzi.
START_RETRY_COOLDOWN_ITERS = 60   # 30 min ciszy, potem kolejna seria prób

# --- Budzenie sesji, gdy wallbox pracuje, a auto nie pobiera ---
# Status WORKING mówi tylko tyle, że wallbox ma otwartą sesję — nie że auto
# bierze prąd. Gdy auto zaśnie (Stellantis po STOPie zamyka sesję i sam jej
# nie wznawia), warunek `not charger_working` nigdy nie jest prawdziwy, więc
# skrypt nie ma jak wysłać STARTu. Jedyne wyjście to cykl STOP -> START, który
# przerywa sygnał na Control Pilot i zmusza auto do nowej negocjacji.
WAKE_CYCLE_AFTER_ITERS  = 30   # 15 min WORKING+0W przy aktywnej chęci ładowania
WAKE_CYCLE_MAX_ATTEMPTS = 2    # twardy limit — każdy STOP to cykl stycznika (Problem 13)

# --- Cena energii ---
NEGATIVE_PRICE_THRESHOLD = 0.0

# --- Interwał pętli ---
UPDATE_INTERVAL_S = 30

# --- Watchdog zamrożonego DP 102 (firmware quirk dé EV v2.9.4) ---
# Gdy w aktywnym trybie ładowania status=WORKING ale moc=0W przez N
# iteracji — wallbox prawdopodobnie zamroził pomiar. Lekarstwo:
# Reboot z aplikacji Smart Life. Watchdog tylko ostrzega w logach.
WATCHDOG_FROZEN_DP_THRESHOLD = 20  # 20 × 30s = 10 minut
# Po ilu iteracjach WORKING+0W przestajemy wierzyć w zero i do obliczenia
# nadwyżki podstawiamy ostatnią znaną moc. Bez tego zamrożony pomiar wygląda
# jak wielki deficyt i skrypt STOPuje realnie trwającą sesję (zamiast dać
# watchdogowi dojść do progu i ostrzec). 2 iteracje = 1 min, czyli więcej niż
# normalna chwila negocjacji auto-wallbox tuż po starcie.
FROZEN_DP_FALLBACK_ITERS = 2

# --- Wykrywanie zawieszenia wallboxa (awaria 2026-08-11) ---
# Firmware dé EV potrafi zawiesić się tak, że urządzenie odpowiada w sieci
# (ping OK, tinytuya czyta bez błędu), ale nie aktualizuje DPS i nie wykonuje
# ŻADNYCH komend — ani START, ani STOP. Trwało to 36 godzin, bo skrypt patrzył
# wyłącznie na power_w == 0, co wygląda identycznie jak auto, które legalnie
# nie pobiera. Sygnatura rozstrzygająca leżała w danych przez cały czas:
# DP 102 identyczny CO DO BITU w kolejnych odczytach. Realne napięcie sieci
# i temperatura obudowy zawsze drgają, a trzy fazy nigdy nie mają dokładnie
# tej samej wartości — zamrożona próbka z awarii to
# {"L1":[2430,0,0],"L2":[2430,0,0],"L3":[2430,0,0],"t":330,...} przez 36 h.
FROZEN_METRICS_THRESHOLD = 10   # 10 × 30 s = 5 min bez zmiany DP 102
# Alarmujemy tylko gdy skrypt w ogóle chce ładować — inaczej spokojny postój
# (auto odpięte, noc) wyglądałby jak awaria. To OKNO tolerancji, nie warunek
# "tryb aktywny w tej iteracji": nadwyżka stojąca na granicy progu przerzuca
# tryb SOLAR/IDLE co kilka iteracji i twardy warunek kasowałby wykrycie
# (2026-08-11 12:27: licznik wyzerował się przy stanie 40).
HEALTH_ACTIVE_GRACE_ITERS = 6   # 3 min od ostatniej chęci ładowania
# Komendy bez efektu — wspólny licznik dla START i STOP. Osobne liczniki nie
# łączyły kropek: wallbox ignorował START (2026-08-10) i STOP (2026-08-11),
# a każdy licznik z osobna mieścił się w swoim limicie i milkł.
UNRESPONSIVE_CMD_THRESHOLD = 4
HEALTH_NOTIFY_ID = "ev_charger_awaria"

# --- Automatyczny restart zawieszonego wallboxa (2026-08-19) ---
# Powód: wykrywanie awarii działa od 2026-08-11 i jest celne, ale jedyną
# reakcją było powiadomienie w panelu HA. Awaria 18-19 sierpnia pokazała, ile
# to kosztuje: wallbox stanął 18.08 ok. 13:00, watchdog alarmował sześć razy,
# a ręka kliknęła Reboot dopiero 19.08 o 11:30. 22,5 godziny martwoty i co
# najmniej 12,2 kWh nadwyżki oddanej do sieci zamiast do auta. Jedyne znane
# lekarstwo (Reboot) da się wysłać automatem, więc alarm bez akcji jest
# marnowaniem informacji, którą skrypt ma od pierwszych 5 minut awarii.
#
# Komenda restartu — patrz _reboot_charger(). Do czasu ustalenia kodu DP
# mechanizm działa "na sucho": wykrywa, loguje i woła człowieka jak dotąd.
REBOOT_DP       = None    # numer DP wywołującego restart (patrz _reboot_charger)
REBOOT_DP_VALUE = True    # wartość kończąca zbocze
# Przerwa między dwiema połówkami zbocza. AppDaemon ma jeden wątek roboczy,
# więc to blokuje pętlę — ale restart zdarza się najwyżej raz na kilka dni,
# a 2,5 s to mniej niż jedna iteracja.
REBOOT_EDGE_GAP_S = 2.5

# Ile prób restartu w jednej awarii. Restart jest tani (wallbox wstaje w ~1 min),
# ale gdy trzy z rzędu nie pomogły, problem jest głębszy niż firmware i dalsze
# klikanie tylko zaciemnia obraz — wtedy woła się człowieka.
REBOOT_MAX_ATTEMPTS   = 3
# Odstęp między próbami. Musi być dłuższy niż FROZEN_METRICS_THRESHOLD (5 min),
# żeby detekcja zdążyła ocenić, czy poprzedni restart pomógł.
REBOOT_COOLDOWN_ITERS = 20    # 20 × 30 s = 10 min
# Po tylu iteracjach zdrowej pracy uznajemy awarię za zamkniętą i licznik prób
# rusza od zera. Bez tego trzy restarty rozłożone na miesiąc wyczerpałyby limit.
REBOOT_ATTEMPTS_RESET_ITERS = 120   # 1 h

# Profilaktyczny restart nocny — higiena na wypadek zawieszeń, które zdążą się
# zacząć i skończyć poza oknem obserwacji. Godzina wybrana tak, by nie kolidować
# z niczym: tryb zimowy ładuje 22:00-6:00, więc dodatkowo pilnujemy, żeby nie
# przerwać realnie trwającego ładowania.
REBOOT_NIGHTLY_HOUR = 4       # None = wyłącz profilaktykę

# Kill switch w HA. Brak encji (get_state -> None) oznacza "włączone", żeby
# mechanizm działał od razu po wdrożeniu, zanim helper powstanie w interfejsie.
AUTO_REBOOT_ENTITY = "input_boolean.ev_auto_restart"

# Push na telefon. Panel HA zostaje jako drugi kanał: awaria 18-19.08 przeleżała
# tam 22 godziny niezauważona, więc telefon jest teraz kanałem podstawowym.
# Celowo konkretne urządzenie, nie grupa "notify/notify": ta ostatnia rozsyła na
# WSZYSTKIE zarejestrowane telefony, więc alarm o wallboxie budziłby też Olę.
# Usługi dostępne w tym HA (sprawdzone 2026-08-20): notify/notify,
# notify/mobile_app_tomek_oneplus_12, notify/mobile_app_ola_samsung_s23,
# notify/persistent_notification, notify/send_message.
NOTIFY_SERVICE = "notify/mobile_app_tomek_oneplus_12"

# Tryby, w których skrypt świadomie chce ładować.
ACTIVE_CHARGING_MODES = ("SOLAR", "EMERGENCY", "NEGATIVE_PRICE", "WINTER_NIGHT")

# --- Tryb zimowy ---
WINTER_MODE_ENTITY  = "input_boolean.ev_tryb_zimowy"
WINTER_MAX_CURRENT  = 10
WINTER_START_HOUR   = 22
WINTER_END_HOUR     = 6

# --- Tryb EMERGENCY ---
EMERGENCY_MODE_ENTITY  = "input_boolean.ev_tryb_awaryjny"
EMERGENCY_HOURS_ENTITY = "input_number.ev_awaryjny_godziny"

# --- Ręczna archiwizacja (przycisk testowy/podglądowy) ---
# Naciśnięcie archiwizuje bieżący miesiąc z aktualnymi danymi, bez resetu
# liczników. Wpis jest idempotentny po "YYYY-MM" — przy realnym przełomie
# miesiąca zostanie nadpisany wartością końcową.
ARCHIVE_NOW_ENTITY = "input_button.ev_archiwizuj_teraz"

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
# IDLEINS złapany snifferem 2026-08-19 15:04:49 — stan przejściowy w sekwencji
# startu sesji: PAUSE -> IDLE -> IDLEINS -> WORKING, trwał 9 sekund
# (najpewniej "idle, cable inserted"). Nie było go na żadnej liście, więc
# _decide() widział status spoza obu zbiorów i zwracał "auto niepodłączone".
# Skutek przy trafieniu pętli w to okno: iteracja bez sterowania, a gdyby
# wallbox zawiesił się właśnie tutaj — watchdog milczy, bo zamrożenie liczy
# się tylko wtedy, gdy skrypt w ogóle chce ładować.
CHARGER_READY_STATES   = {"PAUSE", "SLEEP", "IDLE", "IDLEINS", "UNKNOWN"}
CHARGER_WORKING_STATES = {"WORKING"}


class EVChargerControl(hass.Hass):

    def _init_runtime_state(self):
        """Cały stan runtime w jednym miejscu.

        Wydzielone z initialize(), żeby testy jednostkowe startowały z dokładnie
        tym samym stanem co żywy HA — wcześniej testy powielały tę listę ręcznie
        i rozjeżdżały się z produkcją przy każdym nowym polu.
        """
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
        self._last_nonzero_power_w = 0.0   # do kompensacji przy zamrożonym DP 102
        self._session_start_time  = None
        self._device_error_count  = 0
        self._last_sent_current   = -1
        self._pending_current     = -1   # cel oczekujący na potwierdzenie
        self._pending_iters       = 0    # ile iteracji już się utrzymuje
        self._last_sent_switch    = None
        self._switch_mismatch_iters = 0   # iteracje niezgodności stan vs wysłana komenda
        self._start_retries         = 0   # ile razy ponowiono START w bieżącym podejściu
        self._stop_retries          = 0   # jw. dla STOP
        self._start_giveup_iters    = 0   # ile iteracji trwa cooldown po odpuszczeniu
        self._wake_attempts         = 0   # cykle budzenia w bieżącej sesji
        self._last_charger_status   = None
        self._current_mismatch_iters = 0  # iteracje niezgodności DP 150 vs wysłany prąd
        self._schedule_clears       = 0   # ile razy czyściliśmy wepchnięty harmonogram
        self._iters_schedule_empty  = 0
        self._emergency_end_time  = None
        self._surplus_history         = []
        # --- DIAG bug 2: śledzenie czy wallbox tkwi w WORKING+0W i czy DP 151 się zmienia ---
        self._working_zero_power_streak = 0
        self._last_schedule_seen        = None
        # --- Zdrowie wallboxa (awaria 2026-08-11) ---
        self._last_metrics_raw       = None
        self._frozen_metrics_streak  = 0
        self._iters_since_active_mode = HEALTH_ACTIVE_GRACE_ITERS + 1
        self._unresponsive_cmds      = 0
        self._health_notified        = False
        # --- Automatyczny restart (awaria 2026-08-18/19) ---
        self._reboot_attempts        = 0
        self._reboot_cooldown        = 0   # iteracje do następnej dozwolonej próby
        self._iters_healthy          = 0   # ile iteracji z rzędu wallbox jest zdrowy
        self._reboot_no_cmd_logged   = False
        # Trwałe, żeby restart AppDaemona w środku nocy nie wywołał drugiej
        # profilaktyki tego samego dnia.
        self._last_nightly_reboot_day = self._load_persistent_raw(
            "ev_last_nightly_reboot", "")

    def initialize(self):
        self.log("EV Charger Control startuje...")
        self._init_runtime_state()

        # Odtwórz sensor historii z trwałego pliku (po restarcie HA/AppDaemon)
        self._publish_history()
        # Ręczna archiwizacja na żądanie (przycisk w HA)
        self.listen_state(self._on_archive_now, ARCHIVE_NOW_ENTITY)

        self._device = tinytuya.Device(
            DEVICE_ID, DEVICE_IP, DEVICE_KEY, version=PROTOCOL
        )
        self._device.set_socketTimeout(6)
        # 1 retry, nie 3 — pętla i tak ponawia co 30 s, a 3 retry x 6 s timeout
        # (plus drugi odczyt) potrafiły blokować wątek dłużej niż interwał pętli.
        self._device.set_socketRetryLimit(1)

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
        ha_data = self._get_ha_data(charger_data)
        mode, target_current = self._decide(ha_data, charger_data)
        self._apply_decision(mode, target_current, charger_data)
        self._update_diag(charger_data, mode)
        self._update_health(charger_data, mode)
        self._maybe_nightly_reboot(charger_data)
        self._update_sensors(charger_data, ha_data, mode, target_current)
        self._update_ha_helpers(charger_data, ha_data, mode, target_current)

    # ------------------------------------------------------------------
    # WATCHDOG / DIAGNOSTYKA
    # ------------------------------------------------------------------

    def _update_diag(self, charger_data, mode):
        """Streak WORKING+0W (zasila fallback kompensacji) + ślad zmian DP 151.

        Streak celowo NIE zależy od trybu. Wcześniej liczył się tylko w trybie
        aktywnym i każde zejście do IDLE zerowało go — przy nadwyżce stojącej
        na granicy progu tryb przerzuca się co kilka iteracji, więc licznik
        gubił się w połowie (2026-08-11 12:27: reset przy stanie 40). Traciły
        na tym dwie rzeczy naraz: wykrywanie awarii i fallback kompensacji,
        który bez streaka przestawał podstawiać ostatnią znaną moc i skrypt
        STOPował realnie trwającą sesję.
        """
        worker_no_power = (charger_data["status"] in CHARGER_WORKING_STATES
                           and charger_data["power_w"] == 0)

        if worker_no_power:
            self._working_zero_power_streak += 1
            if self._working_zero_power_streak == WATCHDOG_FROZEN_DP_THRESHOLD:
                # Sam fakt braku poboru nie jest jeszcze diagnozą — auto może
                # być pełne. Rozstrzyga _update_health() po zawartości DP 102.
                self.log(
                    f"Brak poboru mimo statusu WORKING przez "
                    f"{WATCHDOG_FROZEN_DP_THRESHOLD * UPDATE_INTERVAL_S}s "
                    f"(tryb {mode}). DP102_raw={charger_data.get('metrics_raw')!r}",
                    level="WARNING"
                )
        else:
            if self._working_zero_power_streak >= WATCHDOG_FROZEN_DP_THRESHOLD:
                self.log(
                    f"Koniec WORKING+0W "
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

        # Chmura Tuya wpycha harmonogram po każdym reboocie wallboxa
        # (2026-05-21 i 2026-08-11: "ss":"15:00","se":"17:00"). Dotąd czyściliśmy
        # DP 151 tylko w initialize() i _send_start(), więc gdy wallbox siedział
        # w WORKING, harmonogram zostawał i mógł wprowadzić PAUSE o swojej porze.
        if self._schedule_is_set(schedule_now):
            self._iters_schedule_empty = 0
            if self._schedule_clears < SCHEDULE_CLEAR_MAX:
                self._schedule_clears += 1
                self.log(
                    f"Chmura Tuya wepchnela harmonogram {schedule_now!r} — czyszcze "
                    f"({self._schedule_clears}/{SCHEDULE_CLEAR_MAX})", level="WARNING")
                self._clear_schedule()
            elif self._schedule_clears == SCHEDULE_CLEAR_MAX:
                self._schedule_clears += 1   # żeby zalogować tylko raz
                self.log(
                    "Harmonogram wraca mimo czyszczenia — przestaje probowac. "
                    "Sprawdz ustawienia ladowarki w Smart Life.", level="ERROR")
        else:
            self._iters_schedule_empty += 1
            if self._iters_schedule_empty >= SCHEDULE_CLEAR_RESET_ITERS:
                self._schedule_clears = 0

    @staticmethod
    def _schedule_is_set(schedule_raw):
        """Czy DP 151 zawiera realny harmonogram (a nie nasz wyczyszczony wzorzec)."""
        if not schedule_raw:
            return False
        try:
            s = (json.loads(schedule_raw) if isinstance(schedule_raw, str)
                 else schedule_raw)
        except (json.JSONDecodeError, TypeError):
            return False
        if not isinstance(s, dict):
            return False
        return (bool(s.get("m"))
                or s.get("ss", "00:00") != "00:00"
                or s.get("se", "00:00") != "00:00")

    def _update_health(self, charger_data, mode):
        """Czy wallbox w ogóle żyje — i powiadomienie, gdy nie.

        Awaria 2026-08-10/11 trwała 36 godzin, bo jedyną reakcją na zawieszony
        wallbox był WARNING w logu, którego nikt nie czyta. Tu wykrywamy dwie
        niezależne sygnatury i mówimy o nich człowiekowi:

        1. Zamrożony DP 102 — identyczny co do bitu przez FROZEN_METRICS_THRESHOLD
           odczytów. Realny pomiar tak nie wygląda.
        2. Komendy bez efektu — wallbox potwierdza wysyłkę, ale status się nie
           zmienia, niezależnie czy prosimy o START czy o STOP.
        """
        if mode in ACTIVE_CHARGING_MODES:
            self._iters_since_active_mode = 0
        else:
            self._iters_since_active_mode += 1

        # Offline ma własną obsługę (_get_charger_data) — nie mieszamy zamrożenia
        # z brakiem łączności, bo lekarstwo jest inne.
        raw = charger_data.get("metrics_raw")
        if charger_data["online"] and raw:
            if raw != self._last_metrics_raw:
                # Ten odczyt otwiera nową serię, więc 1 a nie 0 — streak liczy
                # odczyty, nie porównania.
                self._frozen_metrics_streak = 1
                self._last_metrics_raw = raw
            elif self._iters_since_active_mode > HEALTH_ACTIVE_GRACE_ITERS:
                # Dłuższy postój (noc, PAUSE w oczekiwaniu na słońce): prąd nie
                # płynie, więc stały pomiar niczego nie dowodzi. Gdyby streak
                # rósł także tutaj, pierwsza iteracja po powrocie nadwyżki
                # dawałaby natychmiastowy fałszywy alarm — a powiadomienie,
                # które myli się co rano, przestaje cokolwiek znaczyć.
                self._frozen_metrics_streak = 0
            else:
                self._frozen_metrics_streak += 1

        frozen       = self._is_charger_frozen()
        unresponsive = self._unresponsive_cmds >= UNRESPONSIVE_CMD_THRESHOLD
        chory        = frozen or unresponsive

        # Cooldown i licznik zdrowia biegną w każdej iteracji, niezależnie od
        # tego, czy akurat alarmujemy — to one decydują, kiedy wolno ponowić
        # restart i kiedy uznać awarię za zamkniętą.
        if self._reboot_cooldown > 0:
            self._reboot_cooldown -= 1
        if chory:
            self._iters_healthy = 0
        else:
            self._iters_healthy += 1
            if (self._reboot_attempts
                    and self._iters_healthy >= REBOOT_ATTEMPTS_RESET_ITERS):
                self.log(
                    f"Wallbox pracuje poprawnie od "
                    f"{self._iters_healthy * UPDATE_INTERVAL_S // 60} min — "
                    f"zeruje licznik restartow ({self._reboot_attempts} w tej awarii)")
                self._reboot_attempts = 0

        if chory:
            powody = []
            if frozen:
                powody.append(
                    f"pomiar DP 102 nie zmienił się od "
                    f"{(self._frozen_metrics_streak - 1) * UPDATE_INTERVAL_S // 60} min"
                )
            if unresponsive:
                powody.append(
                    f"{self._unresponsive_cmds} komend bez efektu "
                    f"(status trzyma się na {charger_data['status']})"
                )
            opis = ", ".join(powody)

            # Najpierw automat, dopiero potem człowiek. Restart jest jedynym
            # znanym lekarstwem, a skrypt wie o awarii 22 godziny wcześniej
            # niż domownik zajrzy do panelu HA.
            if self._try_auto_reboot(opis, raw):
                return

            if not self._health_notified:
                self._health_notified = True
                tekst = (
                    "Ładowarka nie reaguje: " + opis + ". "
                    "Nadwyżka PV idzie do sieci zamiast do auta. " + self._reboot_hint()
                )
                self.log(f"AWARIA WALLBOXA: {tekst} "
                         f"DP102_raw={raw!r}", level="ERROR")
                self._notify_problem(tekst)
                self._notify_push("EV: ładowarka nie reaguje", tekst)
        elif self._health_notified:
            self._health_notified = False
            self.log("Wallbox wrócił do pracy — kasuję powiadomienie o awarii")
            self._dismiss_problem()

    def _reboot_hint(self):
        """Co ma zrobić człowiek — zależnie od tego, czy automat miał czym próbować."""
        if REBOOT_DP is None:
            return ("Lekarstwo: Smart Life -> ładowarka -> Settings -> Reboot "
                    "(NIE Reset to Factory).")
        if self._reboot_attempts >= REBOOT_MAX_ATTEMPTS:
            return (f"Automat restartował ładowarkę {self._reboot_attempts}x bez skutku — "
                    f"to nie jest zwykłe zawieszenie firmware'u. Sprawdź wallbox "
                    f"na miejscu (dioda, wyłącznik nadprądowy, wtyczka w aucie).")
        return ("Automatyczny restart jest wyłączony "
                f"({AUTO_REBOOT_ENTITY}). Lekarstwo ręczne: Smart Life -> "
                "ładowarka -> Settings -> Reboot (NIE Reset to Factory).")

    def _is_charger_frozen(self):
        """Zamrożony wallbox: DP 102 stoi, a my w ostatnich minutach chcieliśmy ładować.

        Drugi warunek odsiewa spokojny postój (auto odpięte, noc) — wtedy stały
        DP 102 niczego złego nie oznacza. Okno HEALTH_ACTIVE_GRACE_ITERS zamiast
        "tryb aktywny teraz", bo tryb potrafi migotać przy nadwyżce na granicy.
        """
        return (self._frozen_metrics_streak >= FROZEN_METRICS_THRESHOLD
                and self._iters_since_active_mode <= HEALTH_ACTIVE_GRACE_ITERS)

    # ------------------------------------------------------------------
    # AUTOMATYCZNY RESTART WALLBOXA
    # ------------------------------------------------------------------

    def _auto_reboot_enabled(self):
        """Kill switch w HA. Brak encji (None) znaczy "włączone" — mechanizm ma
        działać od razu po wdrożeniu, zanim helper powstanie w interfejsie."""
        return self.get_state(AUTO_REBOOT_ENTITY) != "off"

    def _try_auto_reboot(self, opis, raw):
        """Zawieszony wallbox: spróbuj zrestartować, zanim zawołasz człowieka.

        Zwraca True, gdy restart poszedł w tej iteracji — wtedy nie alarmujemy,
        bo za 10 minut (REBOOT_COOLDOWN_ITERS) i tak ocenimy, czy pomogło:
        detekcja zamrożenia liczy od zera, więc martwy wallbox sam się zgłosi.
        """
        if REBOOT_DP is None:
            if not self._reboot_no_cmd_logged:
                self._reboot_no_cmd_logged = True
                self.log(
                    "Auto-restart nieaktywny: nie znam jeszcze komendy restartu "
                    "(REBOOT_DP=None). Zostaje powiadomienie dla czlowieka.",
                    level="WARNING")
            return False
        if not self._auto_reboot_enabled():
            return False
        if self._reboot_cooldown > 0:
            return False
        if self._reboot_attempts >= REBOOT_MAX_ATTEMPTS:
            return False

        self._reboot_attempts += 1
        self.log(
            f"AWARIA WALLBOXA: {opis}. Restartuje automatycznie "
            f"(proba {self._reboot_attempts}/{REBOOT_MAX_ATTEMPTS}). "
            f"DP102_raw={raw!r}", level="ERROR")

        if not self._reboot_charger(f"zawieszenie ({opis})"):
            return False

        if self._reboot_attempts == 1:
            self._notify_push(
                "EV: ładowarka zawieszona",
                f"Wykryto zawieszenie ({opis}). Restartuję ładowarkę automatycznie. "
                f"Odezwę się ponownie tylko wtedy, gdy nie pomoże.")
        elif self._reboot_attempts >= REBOOT_MAX_ATTEMPTS:
            self._notify_push(
                "EV: restart nie pomaga",
                f"To była {self._reboot_attempts}. próba restartu i ładowarka dalej "
                f"nie reaguje. Trzeba sprawdzić wallbox na miejscu.")
        return True

    def _reboot_charger(self, powod):
        """Wyślij komendę restartu. JEDYNE miejsce, które wie JAK to zrobić.

        Wydzielone celowo: jeśli okaże się, że firmware nie wystawia restartu
        po LAN i trzeba będzie odcinać zasilanie przekaźnikiem, zmienia się
        wyłącznie ta metoda — cała logika kiedy/ile razy zostaje bez zmian.
        """
        if REBOOT_DP is None:
            return False
        try:
            # ZBOCZE, nie sama wartość. DP restartu jest typu bool i wyzwala się
            # zmianą stanu — samo wysłanie True nie robi nic, jeśli punkt już w
            # tym stanie siedzi. A nigdy nie wiemy, czy siedzi: punkty tylko do
            # zapisu nie raportują swojej wartości ani w lokalnym odczycie, ani
            # w chmurze (x_do_charge pokazuje tam stan sprzed miesięcy, choć
            # wysyłamy na niego START/STOP codziennie). Bez pauzy urządzenie
            # potrafi połknąć obie zmiany jako jedną paczkę.
            self._device.set_value(REBOOT_DP, not REBOOT_DP_VALUE)
            time.sleep(REBOOT_EDGE_GAP_S)
            self._device.set_value(REBOOT_DP, REBOOT_DP_VALUE)
        except Exception as e:
            self.log(f"Restart wallboxa nie poszedl: {e}", level="ERROR")
            return False

        self.log(f"RESTART WALLBOXA wyslany ({powod})")
        # Wallbox znika z sieci na ~1 min. Wszystko, co opisuje jego stan sprzed
        # restartu, jest już nieaktualne — w szczególności dedup komend: po
        # restarcie urządzenie nie pamięta naszego START-u ani zadanego prądu,
        # więc bez wyzerowania skrypt uznałby, że komendy już wysłał, i zamilkł.
        self._frozen_metrics_streak     = 0
        self._last_metrics_raw          = None
        self._unresponsive_cmds         = 0
        self._working_zero_power_streak = 0
        self._wake_attempts             = 0
        self._start_retries             = 0
        self._stop_retries              = 0
        self._switch_mismatch_iters     = 0
        self._current_mismatch_iters    = 0
        self._last_sent_switch          = None
        self._last_sent_current         = -1
        # Chmura Tuya wpycha harmonogram po każdym restarcie (2026-05-21,
        # 2026-08-11, 2026-08-19) — niech licznik czyszczeń ma pełny limit.
        self._schedule_clears           = 0
        self._reboot_cooldown           = REBOOT_COOLDOWN_ITERS
        return True

    def _maybe_nightly_reboot(self, charger_data):
        """Profilaktyczny restart raz na dobę — higiena przeciw zawieszeniom.

        Nie zastępuje restartu reaktywnego, tylko go uzupełnia: awaria z 18.08
        zaczęła się w środku dnia, więc sama profilaktyka poranna uratowałaby
        wtedy dokładnie nic.
        """
        if REBOOT_NIGHTLY_HOUR is None or REBOOT_DP is None:
            return
        if not self._auto_reboot_enabled():
            return
        now = datetime.datetime.now()
        if now.hour != REBOOT_NIGHTLY_HOUR:
            return
        day = now.strftime("%Y-%m-%d")
        if self._last_nightly_reboot_day == day:
            return
        if not charger_data["online"]:
            return
        # Realnie płynący prąd jest jedynym powodem, by odpuścić: w trybie
        # zimowym auto ładuje się nocą. Sam status WORKING nie wystarcza —
        # zawieszony wallbox też go pokazuje, a jego akurat restartować warto.
        if charger_data["power_w"] > 0:
            return

        self._last_nightly_reboot_day = day
        self._save_persistent("ev_last_nightly_reboot", day)
        self._reboot_charger("profilaktyka nocna")

    def _notify_push(self, tytul, tresc):
        """Push na telefon. Panel HA zostaje jako drugi kanał — awaria
        18-19.08 przeleżała tam 22 godziny, zanim ktokolwiek ją zobaczył."""
        try:
            self.call_service(NOTIFY_SERVICE, title=tytul, message=tresc)
        except Exception as e:
            self.log(f"Push nie poszedl ({NOTIFY_SERVICE}): {e}", level="WARNING")

    def _notify_problem(self, message):
        try:
            self.call_service("persistent_notification/create",
                              title="EV: ładowarka nie reaguje",
                              message=message,
                              notification_id=HEALTH_NOTIFY_ID)
        except Exception as e:
            self.log(f"Blad wysylki powiadomienia: {e}", level="WARNING")

    def _dismiss_problem(self):
        try:
            self.call_service("persistent_notification/dismiss",
                              notification_id=HEALTH_NOTIFY_ID)
        except Exception as e:
            self.log(f"Blad kasowania powiadomienia: {e}", level="WARNING")

    # ------------------------------------------------------------------
    # ODCZYT DANYCH
    # ------------------------------------------------------------------

    def _get_charger_data(self):
        try:
            raw = self._device.status()
            # tinytuya przy problemach sieciowych często NIE rzuca wyjątku,
            # tylko zwraca dict {"Error": ..., "Err": "9xx"} — bez tej detekcji
            # pusty dps dawał status "UNKNOWN", traktowany jako "gotowy do ładowania".
            if (not isinstance(raw, dict) or "Error" in raw
                    or not raw.get("dps", {}).get(str(DP_STATUS))):
                raw = self._device.status()
            if not isinstance(raw, dict) or "Error" in raw:
                raise RuntimeError(f"tinytuya zwrocil blad: {raw!r}")
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

    def _get_ha_data(self, charger_data):
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

        # Nadwyżka bez auta [kW] = min(eksport PCC, PV - dom).
        # Sofar: dodatni PCC = eksport do sieci, ujemny = import.
        # Samo PCC nie wystarcza: falownik w trybie self-use trzyma PCC~0
        # rozładowując magazyn — deficyt byłby niewidoczny i regulacja
        # podkręcałaby prąd kosztem baterii. PV-dom widzi ten deficyt
        # (wychodzi ujemne), a przy pełnej baterii jest równe PCC.
        # min() bierze wariant konserwatywny: nadwyżka dostępna bez ruszania magazynu.
        surplus_without_ev_kw = min(grid_power, pv_power - load_power)

        # Pobór ładowarki siedzi już w load_power — doliczamy go z powrotem,
        # żeby dostać "ile w ogóle jest do dyspozycji dla auta".
        # KOLEJNOŚĆ MA ZNACZENIE: kompensacja PRZED uśrednianiem. Odwrotnie
        # (średnia z próbek mierzonych przy różnej mocy ładowania + bieżąca moc)
        # daje przeszacowanie i skok prądu tuż po starcie sesji.
        available_kw = surplus_without_ev_kw
        if charger_data["status"] in CHARGER_WORKING_STATES:
            power_w = charger_data["power_w"]
            if (power_w == 0
                    and self._working_zero_power_streak >= FROZEN_DP_FALLBACK_ITERS
                    and self._last_nonzero_power_w > 0):
                power_w = self._last_nonzero_power_w
            available_kw += power_w / 1000.0

        # Uśrednianie — wygładzamy migotanie ±0.x kW przez ostatnie 3 odczyty (90s)
        self._surplus_history.append(available_kw)
        if len(self._surplus_history) > PCC_HISTORY_SIZE:
            self._surplus_history.pop(0)
        avg_available_kw = sum(self._surplus_history) / len(self._surplus_history)

        # SURPLUS_BIAS_W to bufor zachęcający do startu (patrz definicja stałej).
        # Bez floora: przy imporcie surplus_w schodzi poniżej zera, dzięki czemu
        # regulacja w trybie SOLAR widzi wielkość deficytu i redukuje prąd / STOPuje.
        surplus_w = avg_available_kw * 1000 + SURPLUS_BIAS_W

        return {
            "soc":              soc,
            "pv_power":         pv_power * 1000,
            "load_power":       load_power * 1000,
            "grid_power":       grid_power,
            "avg_available_kw": avg_available_kw,
            "surplus_w":        surplus_w,
            "price":            price,
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
            return ("NEGATIVE_PRICE", NEGATIVE_PRICE_CURRENT_A)

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
        # surplus zawiera już kompensację poboru ładowarki (patrz _get_ha_data)
        charger_working   = charger_status in CHARGER_WORKING_STATES
        available_surplus = surplus

        self.log(
            f"SOC={soc:.0f}%, PV={ha_data['pv_power']:.0f}W, "
            f"PCC={ha_data['grid_power']:.2f}kW (avg_dost={ha_data['avg_available_kw']:.2f}kW), "
            f"nadwyzka={available_surplus:.0f}W, "
            f"ladowarka={charger_status}, cena={price:.2f}"
        )

        if charger_working:
            if available_surplus < STOP_SURPLUS_W:
                self.log(f"SOLAR->IDLE: nadwyzka={available_surplus:.0f}W < {STOP_SURPLUS_W}W")
                return ("IDLE", 0)
            else:
                target = self._smooth_current(available_surplus)
                self.log(f"SOLAR: reguluję do {target}A")
                return ("SOLAR", target)
        else:
            if available_surplus >= START_SURPLUS_W:
                # Start sesji — bez wygładzania, bierzemy pełną dostępną moc
                self._pending_current, self._pending_iters = -1, 0
                target = self._surplus_to_current(available_surplus)
                self.log(f"SOLAR: startuję {target}A (nadwyzka={available_surplus:.0f}W)")
                return ("SOLAR", target)
            else:
                return ("IDLE", 0)

    def _surplus_to_current(self, surplus_w, last_a=None):
        """Nadwyżka [W] -> prąd [A], opcjonalnie z histerezą wokół progu stopnia.

        Bez `last_a` to gołe przeliczenie (używane przy starcie sesji).
        Z `last_a` dokładamy strefę nieczułości ±CURRENT_STEP_MARGIN_W: żeby
        podnieść prąd, nadwyżka musi przekroczyć próg stopnia z zapasem, i tak
        samo w dół. To zabija przeskoki 10 -> 11 -> 10 przy nadwyżce stojącej
        dokładnie na granicy.
        """
        def step(w):
            return max(MIN_CURRENT_A, min(MAX_CURRENT_A, int(w / (PHASES * VOLTAGE))))

        if last_a is None or last_a <= 0:
            return step(surplus_w)
        up   = step(surplus_w - CURRENT_STEP_MARGIN_W)   # w górę trudniej
        down = step(surplus_w + CURRENT_STEP_MARGIN_W)   # w dół też trudniej
        if up > last_a:
            return up
        if down < last_a:
            return down
        return last_a                                     # wewnątrz strefy nieczułości

    def _smooth_current(self, surplus_w):
        """Wygładzony cel prądu: histereza + potwierdzenie zmiany w czasie.

        Zmianę wysyłamy dopiero, gdy nowy cel utrzyma się CURRENT_HOLD_ITERS
        iteracji z rzędu. Wyjątek: spadek o CURRENT_FAST_DROP_A lub więcej idzie
        natychmiast, bo chroni przyłącze przed przekroczeniem mocy.
        """
        last = self._last_sent_current
        raw  = self._surplus_to_current(surplus_w, last)

        if last <= 0:                      # start sesji — bez wygładzania
            self._pending_current, self._pending_iters = raw, 0
            return raw
        if raw == last:                    # nic się nie zmienia
            self._pending_current, self._pending_iters = last, 0
            return last
        if last - raw >= CURRENT_FAST_DROP_A:
            self._pending_current, self._pending_iters = raw, 0
            self.log(f"Prad: szybka redukcja {last}A -> {raw}A (ochrona przylacza)")
            return raw

        if raw == self._pending_current:
            self._pending_iters += 1
        else:
            self._pending_current, self._pending_iters = raw, 1
        if self._pending_iters >= CURRENT_HOLD_ITERS:
            self._pending_iters = 0
            return raw
        return last                        # jeszcze nie potwierdzone

    # ------------------------------------------------------------------
    # WYKONANIE DECYZJI
    # ------------------------------------------------------------------

    def _apply_decision(self, mode, target_current, charger_data):
        charger_status  = charger_data["status"]
        charger_working = charger_status in CHARGER_WORKING_STATES

        # Zmiana statusu to zmiana warunków (przepięty kabel, wallbox wrócił
        # po reboocie) — liczniki prób ruszają od nowa. _last_sent_switch
        # celowo zostaje: wallbox przeskakuje SLEEP/IDLE całymi blokami i
        # kasowanie go przy każdym przeskoku dałoby lawinę komend.
        if charger_status != self._last_charger_status:
            if self._last_charger_status is not None:
                self._start_retries         = 0
                self._stop_retries          = 0
                self._start_giveup_iters    = 0
                self._switch_mismatch_iters = 0
            self._last_charger_status = charger_status

        if mode in ACTIVE_CHARGING_MODES:
            if target_current > 0 and target_current != self._last_sent_current:
                # _last_sent_current tylko przy sukcesie — porażka wysyłki
                # zostawia różnicę wartości, więc ponowienie wyjdzie za 30 s.
                if self._set_current(target_current):
                    self._last_sent_current      = target_current
                    self._current_mismatch_iters = 0
            else:
                self._verify_current(charger_data)
            if not charger_working:
                if self._last_sent_switch is not True:
                    # Pierwsza próba (albo ponowienie po nieudanej wysyłce).
                    self._send_start()
                elif self._start_retries > SWITCH_MAX_START_RETRIES:
                    # Odpuszczone — ale tylko na czas cooldownu. Warunki mogą
                    # się zmienić same (auto się obudzi, wallbox wróci), więc
                    # po przerwie wracamy do prób zamiast milczeć do wieczora.
                    self._start_giveup_iters += 1
                    if self._start_giveup_iters >= START_RETRY_COOLDOWN_ITERS:
                        self._start_giveup_iters    = 0
                        self._start_retries         = 0
                        self._switch_mismatch_iters = 0
                        self.log(
                            f"Cooldown {START_RETRY_COOLDOWN_ITERS * UPDATE_INTERVAL_S // 60} "
                            f"min minal — wracam do prob STARTu")
                else:
                    # START poszedł, ale wallbox wciąż nie ładuje — ponów
                    # z backoffem, z limitem prób (auto może być po prostu pełne).
                    self._switch_mismatch_iters += 1
                    if self._switch_mismatch_iters >= SWITCH_RETRY_ITERATIONS:
                        self._switch_mismatch_iters = 0
                        # Liczymy niezależnie od tego, czy jeszcze ponawiamy —
                        # po wyczerpaniu limitu wallbox dalej nie reaguje i to
                        # jest właśnie sygnał, który ma dotrzeć do człowieka.
                        self._unresponsive_cmds += 1
                        if self._start_retries < SWITCH_MAX_START_RETRIES:
                            self._start_retries += 1
                            self.log(
                                f"START bez efektu (status={charger_status}) — "
                                f"ponawiam ({self._start_retries}/{SWITCH_MAX_START_RETRIES})",
                                level="WARNING")
                            self._send_start()
                        elif self._start_retries == SWITCH_MAX_START_RETRIES:
                            self._start_retries += 1   # żeby zalogować tylko raz
                            self._start_giveup_iters = 0
                            self.log(
                                f"START ponawiany {SWITCH_MAX_START_RETRIES}x bez efektu "
                                f"(status={charger_status}) — pauza "
                                f"{START_RETRY_COOLDOWN_ITERS * UPDATE_INTERVAL_S // 60} min "
                                f"(auto pelne / odlaczone?)", level="WARNING")
            else:
                if self._try_wake_session(charger_data):
                    return   # cykl budzenia zajął tę iterację
                # Stan zgadza się z intencją — wyzeruj liczniki ponowień.
                self._charger_active        = True
                self._switch_mismatch_iters = 0
                self._start_retries         = 0
                self._stop_retries          = 0
                self._unresponsive_cmds     = 0

        elif mode in ("BATTERY_PRIORITY", "IDLE", "OFFLINE"):
            if charger_working:
                if self._last_sent_switch is not False:
                    if self._set_switch(False):
                        self._last_sent_switch      = False
                        self._switch_mismatch_iters = 0
                    # porażka wysyłki: _last_sent_switch bez zmian -> retry za 30 s
                else:
                    # STOP poszedł, ale wallbox dalej ładuje — ponawiaj co
                    # SWITCH_RETRY_ITERATIONS, ale z limitem: każdy STOP to cykl
                    # stycznika, a wieczne klikanie było Problemem 13.
                    self._switch_mismatch_iters += 1
                    if self._switch_mismatch_iters >= SWITCH_RETRY_ITERATIONS:
                        self._switch_mismatch_iters = 0
                        self._unresponsive_cmds += 1
                        if self._stop_retries < SWITCH_MAX_STOP_RETRIES:
                            self._stop_retries += 1
                            self.log(
                                f"STOP bez efektu (status={charger_status}) — ponawiam "
                                f"({self._stop_retries}/{SWITCH_MAX_STOP_RETRIES})",
                                level="WARNING")
                            self._set_switch(False)
                        elif self._stop_retries == SWITCH_MAX_STOP_RETRIES:
                            self._stop_retries += 1   # żeby zalogować tylko raz
                            self.log(
                                f"STOP ponawiany {SWITCH_MAX_STOP_RETRIES}x bez efektu — "
                                f"wallbox laduje mimo trybu {mode}. Przestaje klikac "
                                f"stycznikiem; sprawdz wallboxa (Reboot z Smart Life?)",
                                level="ERROR")
                self._charger_active     = False
                self._session_start_time = None
                # Sesja zamknięta — nie przenoś oczekującej zmiany prądu na następną
                self._pending_current, self._pending_iters = -1, 0
            else:
                if self._charger_active:
                    self._charger_active = False
                # Ładowarka stoi i o to nam chodziło — stan spójny z intencją
                # "wyłączone". Zapamiętanie tego sprawia, że po powrocie nadwyżki
                # START pójdzie od razu, a nie dopiero po cyklu ponowień.
                self._last_sent_switch      = False
                self._switch_mismatch_iters = 0
                self._start_retries         = 0
                self._stop_retries          = 0
                self._unresponsive_cmds     = 0

    def _verify_current(self, charger_data):
        """Czy wallbox faktycznie przyjął zadany prąd (DP 150).

        Bez tego komenda do martwego urządzenia wygląda jak sukces — 2026-08-11
        skrypt wysłał 6A, 9A, 7A i 8A do zawieszonego wallboxa i każdą uznał za
        wykonaną, bo set_value nie zgłosił błędu.
        """
        # Tylko gdy wallbox realnie pracuje. Stojący potrafi raportować co
        # innego niż ostatnio zadane i weryfikowanie go dawałoby ponowienia
        # co minutę — czyli dokładnie to pikanie, które usuwał Problem 23.
        if (not charger_data["online"]
                or charger_data["status"] not in CHARGER_WORKING_STATES
                or self._last_sent_current <= 0):
            return
        if charger_data["current_a"] == self._last_sent_current:
            self._current_mismatch_iters = 0
            return
        self._current_mismatch_iters += 1
        if self._current_mismatch_iters >= CURRENT_VERIFY_ITERS:
            self._current_mismatch_iters = 0
            self._unresponsive_cmds += 1
            self.log(
                f"Prad {self._last_sent_current}A nie przyjal sie — wallbox "
                f"raportuje {charger_data['current_a']}A. Ponawiam.",
                level="WARNING")
            self._set_current(self._last_sent_current)

    def _try_wake_session(self, charger_data):
        """Wallbox pracuje, ale auto nie pobiera — przerwij i wznów sesję.

        Zwraca True, gdy cykl budzenia zajął tę iterację. START nie jest tu
        wysyłany: po skutecznym STOPie wallbox schodzi z WORKING i normalna
        ścieżka `not charger_working` wyśle START w następnej iteracji.
        """
        if charger_data["power_w"] > 0:
            self._wake_attempts = 0          # realny pobór — sesja żyje
            return False
        if self._working_zero_power_streak < WAKE_CYCLE_AFTER_ITERS:
            return False                     # auto negocjuje, to jeszcze norma
        if self._wake_attempts >= WAKE_CYCLE_MAX_ATTEMPTS:
            return False                     # limit wyczerpany, nie klikamy dalej

        self._wake_attempts += 1
        minutes = self._working_zero_power_streak * UPDATE_INTERVAL_S // 60
        self.log(
            f"Auto nie pobiera od {minutes} min mimo statusu WORKING — "
            f"cykl budzenia {self._wake_attempts}/{WAKE_CYCLE_MAX_ATTEMPTS} "
            f"(STOP teraz, START w nastepnej iteracji)", level="WARNING")
        if self._set_switch(False):
            self._last_sent_switch          = False
            self._switch_mismatch_iters     = 0
            # Zeruj streak, żeby druga próba przyszła po pełnym oknie
            # obserwacji, a nie w następnej iteracji.
            self._working_zero_power_streak = 0
        return True

    def _send_start(self):
        """Wyślij START (z czyszczeniem harmonogramu). _last_sent_switch
        aktualizowany tylko przy udanej wysyłce — inaczej ponowimy za 30 s."""
        self._clear_schedule()
        if not self._set_switch(True):
            return False
        self._last_sent_switch      = True
        self._switch_mismatch_iters = 0
        self._charger_active        = True
        if self._session_start_time is None:
            self._session_start_time  = datetime.datetime.now()
            self._current_session_kwh = 0.0
        return True

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
            return True
        except Exception as e:
            self.log(f"Blad ustawiania pradu: {e}", level="ERROR")
            return False

    def _set_switch(self, on: bool):
        try:
            self._device.set_value(DP_SWITCH, on)
            self.log(f"Ladowarka: {'START' if on else 'STOP'}")
            return True
        except Exception as e:
            self.log(f"Blad przelaczania: {e}", level="ERROR")
            return False

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
            self._save_persistent_many({
                "ev_last_ym":          current_ym,
                "ev_month_energy_kwh": self._month_energy_kwh,
            })
        if self._last_update_time is not None and charger_data["online"]:
            dt_hours   = (now - self._last_update_time).total_seconds() / 3600.0
            energy_kwh = (charger_data["power_w"] * dt_hours) / 1000.0
            if energy_kwh > 0:
                self._current_session_kwh += energy_kwh
                self._month_energy_kwh    += energy_kwh
                self._total_energy_kwh    += energy_kwh
                self._save_persistent_many({
                    "ev_month_energy_kwh": self._month_energy_kwh,
                    "ev_total_energy_kwh": self._total_energy_kwh,
                })
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
        if charger_data["power_w"] > 0:
            self._last_nonzero_power_w = charger_data["power_w"]
        elif charger_data["status"] not in CHARGER_WORKING_STATES:
            # Sesja realnie stoi — nie ma czego kompensować przy następnym starcie.
            self._last_nonzero_power_w = 0.0
        # Zapamiętaj stan liczników miesięcznych — posłuży za snapshot
        # "koniec miesiąca" przy najbliższym przełomie.
        self._um_snapshot = self._read_um_snapshot()

    # ------------------------------------------------------------------
    # ARCHIWUM HISTORII MIESIĘCZNEJ
    # ------------------------------------------------------------------

    def _on_archive_now(self, entity, attribute, old, new, kwargs):
        """Ręczny trigger z przycisku: zarchiwizuj bieżący miesiąc TERAZ.

        Nie resetuje liczników — to tylko snapshot bieżącego (niezamkniętego)
        miesiąca do podglądu. Czyta świeże wartości utility_meter, więc działa
        poprawnie niezależnie od momentu w pętli.
        """
        ym = datetime.datetime.now().strftime("%Y-%m")
        self._um_snapshot = self._read_um_snapshot()
        self.log(f"Reczna archiwizacja biezacego miesiaca {ym} "
                 f"({self._month_energy_kwh:.2f} kWh)")
        self._archive_month(ym, self._month_energy_kwh)

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
        """Opublikuj sensor.ev_historia_miesieczna z całym archiwum w atrybutach.

        UWAGA architektoniczna: AppDaemon `set_state()` na encji `sensor.*` zwraca
        w HA 2026.x błąd 400 (patrz docs, Problem 11 i 18). Archiwum (do 120 miesięcy
        / 10 lat) nie zmieści się też w `input_text` (limit 255 znaków). Dlatego
        publikujemy bezpośrednim POST-em do REST API rdzenia przez proxy supervisora
        — to przyjmuje pełen payload (zweryfikowane: HTTP 201). Encja jest odtwarzana
        przy każdym initialize() (czyli też po restarcie HA, gdy AppDaemon się
        przełącza). Źródłem prawdy pozostaje `ev_history` w pliku persistent.
        """
        if history is None:
            history = self._load_persistent_raw("ev_history", [])
            if not isinstance(history, list):
                history = []
        try:
            token = os.environ.get("SUPERVISOR_TOKEN")
            if not token:
                self.log("Brak SUPERVISOR_TOKEN — pomijam publikacje sensora historii",
                         level="WARNING")
                return
            last_kwh = history[-1]["ev_kwh"] if history else 0
            resp = requests.post(
                "http://supervisor/core/api/states/" + HISTORY_SENSOR,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type":  "application/json",
                },
                json={
                    "state": last_kwh,
                    "attributes": {
                        "friendly_name": "EV Historia miesięczna",
                        "icon":          "mdi:chart-bar",
                        "months":        history,
                        "months_count":  len(history),
                    },
                },
                timeout=10,
            )
            if resp.status_code not in (200, 201):
                self.log(f"Publikacja historii: HTTP {resp.status_code} "
                         f"{resp.text[:200]}", level="WARNING")
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
            # separators + int — input_text ma limit 255 znaków, margines był wąski
            data = json.dumps({
                "status":                  charger_data["status"],
                "mode":                    mode,
                "power":                   int(round(charger_data["power_w"])),
                "current":                 charger_data["current_a"],
                "target_current":          target_current,
                "session":                 round(self._current_session_kwh, 3),
                "month":                   round(self._month_energy_kwh, 3),
                "total":                   round(self._total_energy_kwh, 3),
                "surplus_w":               int(round(ha_data["surplus_w"])),
                "soc":                     ha_data["soc"],
                "emergency_remaining_min": emergency_remaining_min,
            }, separators=(",", ":"))
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

    def _read_persistent_file(self):
        """Wczytaj cały plik persistent. Uszkodzony JSON (np. crash w trakcie
        zapisu starym kodem) odkładamy jako .corrupt i startujemy od pustego —
        inaczej każdy kolejny zapis padał w nieskończoność na json.load."""
        try:
            if not os.path.exists(_PERSIST_PATH):
                return {}
            with open(_PERSIST_PATH, "r") as f:
                return json.load(f)
        except ValueError as e:
            corrupt = _PERSIST_PATH + ".corrupt"
            try:
                os.replace(_PERSIST_PATH, corrupt)
                self.log(f"Uszkodzony {_PERSIST_PATH} ({e}) — kopia w {corrupt}, "
                         f"zaczynam od pustego", level="ERROR")
            except OSError as e2:
                self.log(f"Blad odczytu persistent: {e}; nie udalo sie odlozyc kopii: {e2}",
                         level="ERROR")
            return {}
        except Exception as e:
            self.log(f"Blad odczytu persistent: {e}", level="WARNING")
            return {}

    def _write_persistent_file(self, data):
        """Zapis atomowy (tmp + os.replace) — crash w trakcie zapisu nie może
        zniszczyć pliku z licznikami i 10-letnim archiwum."""
        try:
            tmp = _PERSIST_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(data, f)
            os.replace(tmp, _PERSIST_PATH)
        except Exception as e:
            self.log(f"Blad zapisu persistent: {e}", level="WARNING")

    def _save_persistent(self, key, value):
        self._save_persistent_many({key: value})

    def _save_persistent_many(self, updates):
        """Kilka kluczy w jednym read-modify-write (mniej I/O w pętli 30 s)."""
        data = self._read_persistent_file()
        data.update(updates)
        self._write_persistent_file(data)

    def _load_persistent(self, key, default):
        try:
            return float(self._read_persistent_file().get(key, default))
        except (TypeError, ValueError):
            return default

    def _load_persistent_raw(self, key, default):
        """Jak _load_persistent, ale bez rzutowania na float — dla stringów/list/dictów."""
        return self._read_persistent_file().get(key, default)
