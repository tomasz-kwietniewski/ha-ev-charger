Mam fotowoltaikę 9 kWp, magazyn energii 15 kWh i Citroëna Spacetourer elektrycznego z baterią 75 kWh. Przez chwilę ładowałem auto "na ślepo" — podłączałem kabel i tyle. Tymczasem latem moje panele produkują więcej prądu niż potrzebuję, a nadwyżki szły do sieci. Postanowiłem to zmienić.

Problem: moja ładowarka — **dé EV Charger 11 kW z Wi-Fi** za około 1150 zł — teoretycznie nie oferuje żadnych zaawansowanych konfiguracji sterowania mocą. Łączy się z chmurą Tuya przez aplikację Smart Life i tyle. Nie ma API, nie ma integracji z Home Assistant, nie ma możliwości ustawienia "ładuj tylko z nadwyżek".

A jednak udało się to osiągnąć. Oto jak — i na co uważać.

---

## Co mam w domu

- **Fotowoltaika:** 9 kWp (18 paneli JA Solar n-type)
- **Falownik hybrydowy:** Sofar HYD 8KTL-3PH
- **Magazyn energii:** Sofar BTS E15-DS5 (15 kWh)
- **Auto:** Citroën Spacetourer Electric 75 kWh
- **Ładowarka:** dé EV Charger 11 kW, 3-fazowa, Wi-Fi, protokół Tuya
- **Centrum automatyki:** Home Assistant na Synology NAS DS420+
- **Taryfa:** Pstryk (dynamiczne ceny energii)

---

## Dlaczego Local Tuya nie zadziałało — protokół 3.5

Zanim doszedłem do rozwiązania z TinyTuya, próbowałem najprostszej drogi: integracji **Local Tuya** dostępnej przez HACS. To popularna integracja która pozwala sterować urządzeniami Tuya lokalnie bezpośrednio z Home Assistant — bez żadnego kodowania w Pythonie.

Przeprowadziłem szczegółową diagnostykę. Połączenie TCP z ładowarką na porcie 6668 działało prawidłowo, Local Key był poprawny, adres IP również. Problem tkwił gdzie indziej — dwa czynniki jednocześnie:

**Problem 1: Protokół 3.5**
Ładowarka dé EV używa protokołu Tuya w wersji **3.5**, natomiast integracja Local Tuya obsługuje tylko wersje do **3.4**. To powodowało że wszystkie encje pozostawały w stanie `unavailable` mimo prawidłowej konfiguracji.

**Problem 2: Brak UDP discovery**
Ładowarka nie wysyłała broadcastu UDP discovery, którego Local Tuya oczekuje do automatycznego wykrycia urządzenia w sieci. Nawet ręczne wpisanie IP nie pomagało — integracja nie mogła nawiązać poprawnej sesji.

Po diagnozie napisałem do sprzedawcy ładowarki z raportem technicznym. Odpowiedź: *"dziękujemy, przekażemy do działu technicznego"*. Czekamy na aktualizację Local Tuya lub firmware ładowarki.

**Rozwiązanie: AppDaemon + TinyTuya**

Biblioteka TinyTuya obsługuje protokół 3.5 i połączyła się bez problemów. Uruchomiłem ją przez add-on AppDaemon w Home Assistant, który pozwala instalować paczki Pythona trwale.

Oficjalne nazwy Data Pointów pobrane przez Tuya IoT Platform:

| DP  | Nazwa w API      | Znaczenie                                          |
| --- | ---------------- | -------------------------------------------------- |
| 101 | x_work_state     | Stan pracy jako liczba                             |
| 102 | x_metrics        | Dane pomiarowe JSON (napięcie/prąd/moc per faza) |
| 109 | x_work_st_debug  | Status: WORKING / SLEEP / IDLE / PAUSE             |
| 140 | x_do_charge      | Start/Stop ładowania (bool)                       |
| 150 | x_charge_current | Prąd ładowania w amperach                        |

**Protokół 3.5 — to kluczowa informacja** dla każdego kto będzie chciał zintegrować tę ładowarkę z Home Assistant. Local Tuya nie zadziała. Jedyna działająca droga to AppDaemon + TinyTuya.

Jeśli kiedyś Local Tuya doda obsługę protokołu 3.5, konfiguracja stanie się znacznie prostsza — wystarczy dodać urządzenie przez UI bez pisania kodu.

---

## Kluczowe odkrycie: TinyTuya i lokalna kontrola

Urządzenia Tuya (Smart Life) domyślnie komunikują się przez chmurę producenta. Każde kliknięcie w aplikacji wędruje przez serwery Tuya i wraca do urządzenia. To oznacza zależność od internetu, opóźnienia i — co ważne — **limity zapytań w darmowym planie API** (około 1000 dziennie).

Dla użytkowników z Polski dane trafiają na serwer w **Frankfurcie** (AWS, Niemcy) — nie w Chinach jak można by się spodziewać. Tuya ma centra danych w Europie Centralnej i Zachodniej obsługujące europejskich użytkowników. Opóźnienia są więc minimalne, ale limit zapytań pozostaje problemem.

Ale jest sposób, żeby to obejść. Biblioteka **TinyTuya** pozwala komunikować się z urządzeniem **bezpośrednio po sieci lokalnej**, bez udziału chmury. Wystarczy znać trzy rzeczy:

- **Device ID** — unikalny identyfikator urządzenia
- **Local Key** — klucz szyfrujący (pobierany jednorazowo z chmury Tuya)
- **IP urządzenia** — lokalny adres w sieci domowej

Po jednorazowym pobraniu klucza z chmury (przez Tuya IoT Platform), całe sterowanie odbywa się lokalnie. Zero limitów, zero opóźnień, zero zależności od internetu.

```python
import tinytuya

d = tinytuya.Device(
    dev_id="TWOJ_DEVICE_ID",
    address="192.168.X.X",
    local_key="TWOJ_LOCAL_KEY",
    version=3.5
)

status = d.status()
print(status)
```

---

## Data Pointy — jak ładowarka mówi o sobie

Urządzenia Tuya komunikują się przez tzw. **Data Pointy (DP)** — numerowane kanały danych. Każde urządzenie ma swój zestaw DP. Żeby dowiedzieć się co DP znaczą, trzeba zapytać urządzenie i przeanalizować odpowiedź w różnych stanach pracy.

Kluczowa pułapka: klucze w słowniku `dps` są **stringami**, nie integerami. Dlatego `dps.get(109)` zawsze zwróci `None` — trzeba używać `dps.get("109")`. To jeden z tych błędów który potrafi zająć godzinę debugowania.

### Pełna mapa Data Pointów

Po dogłębnej analizie udało mi się rozszyfrować wszystkie DP tej ładowarki:

| DP  | Typ    | Znaczenie                                                 | Przydatność                  |
| --- | ------ | --------------------------------------------------------- | ------------------------------ |
| 102 | JSON   | Dane pomiarowe per faza (napięcie, prąd, moc)           | ⭐⭐⭐ używam                 |
| 105 | JSON   | Historia ostatniej sesji (start, koniec, czas, prąd)     | ⭐⭐⭐ bardzo przydatne        |
| 106 | JSON   | Dane techniczne (wersja firmware, parametry)              | ⭐ informacyjne                |
| 107 | string | Lista dostępnych poziomów prądu:`[6, 8, 10, 13, 16]` | ⭐⭐ warto znać               |
| 109 | string | Status:`WORKING` / `SLEEP` / `IDLE` / `PAUSE`     | ⭐⭐⭐ używam                 |
| 140 | bool   | Start/Stop ładowania                                     | ⭐⭐⭐ używam                 |
| 150 | int    | Prąd ładowania w A (6–16)                              | ⭐⭐⭐ używam                 |
| 151 | JSON   | Wbudowany harmonogram ładowania                          | ⭐⭐ alternatywa dla AppDaemon |
| 152 | int    | Maksymalny prąd (16A)                                    | ⭐ informacyjne                |
| 155 | bool   | Nieznane — prawdopodobnie blokada ładowania             | ❓ do zbadania                 |
| 156 | bool   | Nieznane — może tryb jednofazowy/trójfazowy            | ❓ do zbadania                 |
| 157 | int    | Nieznane (zawsze 1)                                       | ❓ do zbadania                 |
| 188 | bool   | Nieznane — może lock kabla                              | ❓ do zbadania                 |

### DP 105 — historia sesji gotowa do odczytu

Ten DP zawiera dane o ostatnim ładowaniu bez potrzeby własnych liczników:

```json
{
  "t": "2026-04-30 17:13:26",
  "s": "17:13",
  "e": "17:41",
  "d": 1677,
  "c": 17
}
```

Gdzie `s` = godzina startu, `e` = godzina końca, `d` = czas trwania w sekundach (1677s ≈ 28 min), `c` = prąd w A.

### DP 102 — dane pomiarowe i ukryta skala

Format danych pomiarowych z DP 102:

```json
{
  "L1": [2260, 144, 32],
  "L2": [2260, 147, 33],
  "L3": [2260, 145, 32],
  "p": 98,
  "e": 11
}
```

Gdzie `L1[2]`, `L2[2]`, `L3[2]` to moc per faza, a `p` to łączna moc — **mnożona przez 100** (98 × 100 = 9800 W = 9,8 kW). Uwaga: nigdzie w dokumentacji tego nie ma — odkryłem to porównując wartości z aplikacją Smart Life.

### DP 151 — wbudowany harmonogram

Ładowarka ma własny harmonogram który można programować:

```json
{"m": 0, "dt": 0, "ss": "15:00", "se": "17:00"}
```

W prostszych przypadkach (np. "ładuj zawsze w nocy 23:00–6:00") można ustawić harmonogram bezpośrednio bez AppDaemon. Do dynamicznego sterowania zależnego od cen i PV — AppDaemon jest niezastąpiony.

---

## Logika sterowania

Napisałem aplikację AppDaemon (Python działający w ramach Home Assistant), która co 30 sekund:

1. Odpytuje ładowarkę przez TinyTuya (lokalnie)
2. Sprawdza dane z falownika Sofar (przez Modbus)
3. Sprawdza aktualną cenę energii Pstryk
4. Podejmuje decyzję i wysyła polecenie do ładowarki

### Tryby pracy

**🔴 BATTERY_PRIORITY** — gdy SOC baterii < 95%
Bateria ma pierwszeństwo. Ładowarka auta pozostaje wyłączona. Falownik ładuje magazyn z PV.

**🟡 SOLAR** — gdy SOC ≥ 95% i nadwyżka PV ≥ 5 kW
Bateria jest pełna, jest nadwyżka słońca. Włącz ładowanie auta. Reguluj prąd dynamicznie w zależności od dostępnej nadwyżki.

**🟢 NEGATIVE_PRICE** — gdy cena Pstryk < 0 zł/kWh
Prąd jest ujemny — czyli operator **płaci** za pobieranie. Ładuj na maksimum (16A = ~11 kW), niezależnie od stanu baterii i PV.

**⚪ IDLE** — brak warunków do ładowania
Ładowarka wyłączona lub auto niepodłączone.

### Statusy ładowarki

Ładowarka raportuje cztery stany przez DP 109:

- `WORKING` — aktywne ładowanie, auto pobiera prąd
- `SLEEP` — auto podłączone, ładowanie oczekuje (np. po podłączeniu kabla)
- `PAUSE` — ładowanie wstrzymane przez zewnętrzną komendę — **to nasz skrypt zatrzymał ładowanie**
- `IDLE` — ładowarka podłączona do prądu, auto niepodłączone

Różnica między `SLEEP` a `PAUSE` jest istotna: `SLEEP` to stan w którym ładowarka czeka sama z siebie, `PAUSE` to stan w którym zatrzymaliśmy ją my przez komendę `set_value(DP_SWITCH, False)`. Odkryłem to dopiero w praktyce — dokumentacja tego nie opisuje.

### Histereza

Żeby uniknąć ciągłego włączania i wyłączania przy zmiennym nasłonecznieniu:

- **Włącz** ładowanie gdy nadwyżka ≥ 5000 W
- **Wyłącz** ładowanie gdy nadwyżka < 3500 W

### Regulacja prądu

```python
def _surplus_to_current(self, surplus_w):
    current = surplus_w / (3 * 230)  # 3 fazy × 230V
    return max(6, min(16, int(current)))
```

Nadwyżka 5000 W → 7A, nadwyżka 11000 W → 16A (max).

### Korekta pomiaru

Gdy ładowarka pracuje, jej moc jest wliczona w zużycie domu. Dlatego eksport do sieci jest zaniżony o moc ładowania. Skrypt to koryguje:

```python
if charger_working:
    available_surplus = grid_export + charger_power_w
else:
    available_surplus = grid_export
```

---

## Architektura techniczna

```
Panele PV → Falownik Sofar → Sieć domowa
                ↓                    ↓
          Magazyn 15 kWh      Ładowarka EV (Tuya)
                                     ↑
                          Home Assistant (NAS)
                                     ↑
                          AppDaemon + TinyTuya
                                     ↑
                          Sofar Modbus + Pstryk API
```

**Home Assistant** zbiera dane ze wszystkich źródeł i wyświetla je na jednym dashboardzie.

**AppDaemon** to środowisko do uruchamiania skryptów Pythona wewnątrz Home Assistant — idealny do logiki wymagającej ciągłego działania co N sekund.

**TinyTuya** komunikuje się z ładowarką lokalnie przez TCP na porcie 6668, używając szyfrowania AES z lokalnym kluczem.

---

## Dashboard w Home Assistant

Po skonfigurowaniu wszystkiego, dashboard pokazuje w jednym miejscu:

- SOC baterii i status magazynu
- Status ładowarki (Ładowanie / Gotowy / Niepodłączone)
- Tryb automatyki (Nadwyżki PV / Priorytet baterii / Ujemna cena)
- Aktualny prąd i moc ładowania
- Energię naładowaną w sesji, miesiącu i łącznie
- Produkcję PV, zużycie domu, status sieci
- Aktualną cenę Pstryk i koszty energii

---

## Problemy które napotkałem — i jak je rozwiązałem

Droga do działającego systemu nie była prosta. Oto pułapki na które wpadłem, żebyś Ty nie musiał.

### Problem 1: Klucze DP jako stringi, nie integery

Pierwsza i najbardziej zdradliwa pułapka. Przez długi czas skrypt zwracał `None` dla wszystkich odczytów statusu ładowarki. Kod wyglądał poprawnie, a błąd był niewidoczny:

```python
status = dps.get(109)  # zawsze None!
```

**Dlaczego?** TinyTuya deserializuje JSON z ładowarki i zostawia klucze jako stringi — tak jak przyszły. Python nie konwertuje `"109"` na `109` automatycznie. To subtelna różnica między typami która nie rzuca żadnego wyjątku.

```python
status = dps.get("109")  # działa!
```

### Problem 2: Ładowarka nie odpowiada gdy intensywnie ładuje

Gdy auto pobierało pełną moc (~10 kW), skrypt zwracał `status=UNKNOWN` i `moc=0W` — mimo że ładowanie działało prawidłowo i Smart Life pokazywało poprawne dane.

**Dlaczego?** Ładowarka ma jeden procesor obsługujący jednocześnie ładowanie i komunikację sieciową. Przy pełnym obciążeniu nie zdąża odpowiedzieć w domyślnym czasie 5 sekund.

Rozwiązanie: wydłużenie timeoutu i dodanie automatycznego retry:

```python
self._device.set_socketTimeout(6)
self._device.set_socketRetryLimit(3)

raw = self._device.status()
if not raw.get('dps', {}).get('109'):
    raw = self._device.status()  # drugi strzał
```

### Problem 3: Ładowarka pikowała co 30 sekund

Skrypt co 30 sekund wysyłał komendę ustawienia prądu — nawet gdy wartość się nie zmieniła. Ładowarka reagowała na każdą komendę sygnałem dźwiękowym. Słyszałem nieustanne pikanie przez uchylone okno.

**Dlaczego?** Ładowarka traktuje każdą przychodzącą komendę jako zdarzenie i potwierdza ją dźwiękiem — niezależnie czy wartość się zmieniła.

Rozwiązanie: zapamiętaj ostatnio wysłaną wartość i wysyłaj tylko gdy coś się zmienia:

```python
if target_current != self._last_sent_current:
    self._set_current(target_current)
    self._last_sent_current = target_current
```

### Problem 4: AppDaemon nie może tworzyć sensorów z atrybutami

Próbowałem tworzyć sensory przez `set_state()` z atrybutami `unit_of_measurement` i `device_class`. Skrypt działał, ale HA zwracał błąd `400 Bad Request` bez żadnego pomocnego komunikatu.

**Dlaczego?** HA 2026.x zaostrzyło walidację — encje tworzone dynamicznie przez API nie mogą mieć atrybutów zastrzeżonych dla encji zarejestrowanych przez integracje.

Rozwiązanie: zamiast tworzyć sensory przez AppDaemon, zapisuję dane do `input_text` jako JSON, a template sensory w `configuration.yaml` parsują te dane:

```yaml
- name: "EV Moc Ladowania"
  state: "{{ (states('input_text.ev_data') | from_json).power | default(0) }}"
  unit_of_measurement: "W"
```

### Problem 5: "Duchy" starych encji blokują nowe

Gdy próbowałem zastąpić stare helpery nowymi o tej samej nazwie, HA pamiętał stare encje z bazy danych i wyświetlał je jako `unavailable` — nowe dostawały przyrostek `_2` w nazwie.

**Dlaczego?** HA przechowuje historię wszystkich encji w bazie SQLite. Nawet po usunięciu helpera z konfiguracji, stary wpis w bazie "przejmuje" nazwę i blokuje nowy.

Rozwiązanie: usunięcie starych wpisów bezpośrednio z bazy:

```bash
sqlite3 /config/home-assistant_v2.db \
  "DELETE FROM states WHERE entity_id='input_number.ev_charger_power';"
```

### Problem 6: Znak eksportu w Sofar — nie zakładaj że jest stały

To był najbardziej podstępny problem, bo objawił się dopiero po kilku dniach działania systemu: ładowarka przestała reagować na nadwyżki PV mimo że słońce świeciło i bateria była pełna. Logi pokazywały `eksport=0W`, dashboard pokazywał eksport 6.5 kW.

**Dlaczego?** Sensor `sofar_modbus_inverter_active_power_pcc_total` zmienił znak eksportu po zmianie trybu pracy falownika. Sofar HYD nie ma jednej ustalonej konwencji — w trybie Self-use eksport był u mnie wartością ujemną, po przełączeniu na Time of Use stał się dodatnią. Nie jest to opisane w dokumentacji. To znane zjawisko wśród użytkowników Sofara w społeczności Home Assistant.

Historia w kodzie:

```python
# Pierwotnie — eksport ujemny (tryb Self-use)
surplus_w = max(0, -grid_power * 1000)

# Po zmianie trybu na Time of Use — eksport dodatni
surplus_w = max(0, grid_power * 1000)   # ← aktualna wersja
```

**Czy może się powtórzyć?** Tak. Jeśli zmienisz tryb pracy falownika (np. przełączysz na Passive Mode do testów) lub wgrasz aktualizację firmware — znak może się odwrócić i skrypt przestanie ładować auto mimo słońca.

**Jak sprawdzić gdy coś nie działa:** Wejdź w HA Developer Tools → States → wyszukaj `sofar_modbus_inverter_active_power_pcc_total`. Gdy wiesz że eksportujesz do sieci (słońce świeci, bateria pełna) — sprawdź czy wartość jest dodatnia czy ujemna. Jeśli ujemna, zmień w skrypcie `grid_power * 1000` na `-grid_power * 1000` i zrestartuj AppDaemon.

### Problem 7: Jednostki w DP 102 nie są oczywiste

Przez długi czas odczytywałem moc z pola `L1[2]` i dostawałem wartość 32 — myśląc że to 32W. Tymczasem ładowarka pobierała ~3200W na fazę.

**Dlaczego?** Ładowarka zwraca moc w skali ×100 — bez żadnej dokumentacji która by to wyjaśniała. Odkryłem to porównując wartość `"p": 98` z aplikacją Smart Life która pokazywała 9,8 kW — czyli 98 × 100 = 9800W. Zawsze weryfikuj jednostki DP z innym źródłem.

---

### Problem 8: Harmonogram w ładowarce blokuje START

Po kilku dniach testów ładowarka wchodziła w stan PAUSE i nie reagowała na komendy START z AppDaemon mimo że wszystkie warunki były spełnione (SOC=100%, nadwyżka PV=6.5 kW).

**Dlaczego?** Ładowarka miała zapisany harmonogram ładowania (DP 151) ustawiony domyślnie przez aplikację Smart Life: `{"ss":"15:00","se":"17:00"}`. Poza oknem 15:00-17:00 ładowarka automatycznie przechodziła w PAUSE i ignorowała zewnętrzne komendy START — nawet gdy harmonogram był "wyłączony" w UI aplikacji.

Rozwiązanie — wyczyść harmonogram przez TinyTuya:

```python
self._device.set_value("151", json.dumps({"m":0,"dt":0,"ss":"00:00","se":"00:00"}))
```

I dodaj to czyszczenie przy każdym starcie skryptu w metodzie `initialize()` — żeby harmonogram nigdy nie blokował automatyki.

### Problem 9: Eksport = 0W gdy auto ładuje — błędna logika zatrzymania

Gdy ładowarka pracowała i pobierała np. 5 kW z nadwyżki PV, eksport do sieci wynosił 0W (bo auto brało całą nadwyżkę). Skrypt interpretował `eksport=0W` jako brak nadwyżki i wysyłał STOP — co było błędem.

**Dlaczego?** Logika korekty była:

```python
available_surplus = eksport + moc_auta  # np. 0 + 5000 = 5000W
```

Ale jednocześnie warunek zatrzymania sprawdzał `available_surplus < 3500W` — co dawało 5000W > 3500W, więc powinno działać. Problem był subtelniejszy: gdy Sofar raportował eksport=0 i jednocześnie moc auta=0 (bo ładowarka była w PAUSE i nie mierzyła prądu), skrypt liczył `0 + 0 = 0W` i zatrzymywał ładowanie.

Rozwiązanie: gdy auto jest w WORKING i eksport bliski zeru (< 500W), nie zatrzymuj ładowania — to znaczy że ładowarka idealnie bilansuje produkcję z poborem:

```python
if charger_working and surplus < 500:
    available_surplus = STOP_SURPLUS_W + 100  # nie zatrzymuj
```

Efekt końcowy: auto ładuje się z nadwyżek PV, eksport = 0W (ładowarka bierze dokładnie tyle ile produkują panele), sieć zbilansowana.

To równie ważna część artykułu. Oto ścieżki które wyglądają sensownie, ale prowadzą donikąd.

### Nie używaj chmury Tuya do sterowania

Darmowy plan Tuya IoT Platform ma limit ~1000 zapytań dziennie. Przy odpytywaniu co 30 sekund to 2880 zapytań — przekroczysz limit przed południem. Chmura jest potrzebna **tylko raz** do pobrania Local Key. Potem wyłącz i zapomnij, używaj TinyTuya lokalnie.

### Nie próbuj sterować przez integrację Tuya/Xtend Tuya w HA

Xtend Tuya jest świetna do prostego włącz/wyłącz przez UI. Ale do dynamicznego sterowania co 30 sekund z logiką zależną od wielu sensorów — jest zbyt ograniczona. AppDaemon + TinyTuya daje pełną kontrolę.

### Nie twórz sensorów dynamicznie przez AppDaemon set_state()

W starszych wersjach HA to działało. W HA 2026.x API odrzuca encje z atrybutami `unit_of_measurement` i `device_class` tworzonymi przez AppDaemon — błąd `400 Bad Request` bez pomocnego komunikatu. Straciłem na tym sporo czasu.

### Nie definiuj helperów w configuration.yaml jeśli chcesz je modyfikować programowo

Encje zdefiniowane w YAML są read-only dla serwisów HA — zawsze pokazują `unavailable` gdy próbujesz je zmienić przez `call_service`. Twórz helpery wyłącznie przez UI (Settings → Helpers → Add).

### Nie ignoruj histerzy

Bez histerzy przy zmiennym nasłonecznieniu ładowarka włącza i wyłącza się co kilka minut. Ładowarka piszczy, auto się denerwuje. Histereza (włącz przy 5 kW, wyłącz przy 3,5 kW) rozwiązuje problem.

---

## Efekty i wnioski

### Strategia sezonowa — lato i zima

System jest zaprojektowany na cały rok z jednym przełącznikiem sezonowym.

**Lato (kwiecień–wrzesień):**
Polska ma dobre nasłonecznienie — 9 kWp produkuje regularnie nadwyżki powyżej 5 kW. Auto ładuje się za darmo z nadwyżek PV. Przy ujemnych cenach Pstryk (które latem zdarzają się regularnie w południe) system ładuje na maksimum — operator energii dopłaca za pobieranie prądu.

**Zima (październik–marzec):**
Krótkie dni, niskie słońce — nadwyżki PV są rzadkie i małe. Jednocześnie od października obowiązuje taryfa G12W z tanią energią nocną (~0.70 zł/kWh vs ~0.85 zł/kWh w dzień). Włączam jeden przełącznik w HA — `❄️ Tryb zimowy` — i skrypt automatycznie ładuje auto w nocy między 22:00 a 6:00 na 10A (~2.3 kW).

Dlaczego 10A a nie 16A? Zimą działają pompy ciepła powietrze-powietrze które mogą pobierać łącznie 3-4 kW. Przy przyłączu 11 kW zostaje bezpiecznie ~7 kW na auto, ale przyjąłem 10A (2.3 kW) jako bezpieczny bufor na szczyty poboru (gotowanie, bojler, klimatyzatory).

Słoneczne dni zimą? Skrypt nadal wykrywa nadwyżki PV i uruchamia tryb SOLAR automatycznie — tryb zimowy dodaje tylko nocne okno ładowania, nie wyłącza logiki solarnej.

Przełączenie jest ręczne — 1 października włączam, 1 kwietnia wyłączam. Można to zautomatyzować przez automatyzację HA opartą na dacie, ale wolę mieć kontrolę i obserwować jak system zachowuje się w różnych warunkach.

**Przy ujemnych cenach Pstryk** (które latem zdarzają się regularnie w godzinach 10:00–16:00) system automatycznie ładuje auto na maksimum. W majowy dzień cena spadła do -0,60 zł/kWh — za każdą godzinę ładowania (9,8 kWh) operator energii **płacił mi** 5,88 zł zamiast żebym ja płacił.

**Ładowanie z nadwyżek** działa dokładnie tak jak planowałem — gdy bateria jest pełna i słońce produkuje więcej niż potrzeba, auto dostaje resztę. Prąd reguluje się co 30 sekund.

---

## Koszt całego rozwiązania

| Element                                         | Koszt               |
| ----------------------------------------------- | ------------------- |
| Ładowarka dé EV 11kW Wi-Fi                    | ~1150 zł           |
| Home Assistant                                  | 0 zł (open source) |
| AppDaemon                                       | 0 zł (open source) |
| TinyTuya                                        | 0 zł (open source) |
| Tuya IoT Platform (jednorazowe pobranie klucza) | 0 zł               |

**Łącznie: 1150 zł** za inteligentną ładowarkę zintegrowaną z PV.

Dla porównania — dedykowane ładowarki z zarządzaniem mocą i integracją z PV kosztują 3000–8000 zł.

---

## Dla technicznych: kluczowe fragmenty kodu

Pełny skrypt AppDaemon dostępny na moim GitHubie (link wkrótce). Poniżej kluczowe fragmenty:

**Pobieranie danych z ładowarki:**

```python
def _get_charger_data(self):
    raw = self._device.status()
    dps = raw.get("dps", {})

    status = str(dps.get("109", "unknown")).upper()
    current = int(dps.get("150", 0))

    metrics = json.loads(dps.get("102", "{}"))
    l1 = metrics.get("L1", [0, 0, 0])
    l2 = metrics.get("L2", [0, 0, 0])
    l3 = metrics.get("L3", [0, 0, 0])
    power_w = (l1[2] + l2[2] + l3[2]) * 100  # skala x100!

    return {"status": status, "current_a": current, "power_w": power_w}
```

**Logika decyzyjna:**

```python
def _decide(self, ha_data, charger_data):
    price = ha_data["price"]
    soc = ha_data["soc"]
    surplus = ha_data["surplus_w"]

    if price < 0:
        return ("NEGATIVE_PRICE", 16)

    if soc < 95:
        return ("BATTERY_PRIORITY", 0)

    if surplus >= 5000:
        current = max(6, min(16, int(surplus / (3 * 230))))
        return ("SOLAR", current)

    return ("IDLE", 0)
```

---

## Podsumowanie

Inteligentne ładowanie auta elektrycznego z nadwyżek PV nie wymaga drogiego sprzętu. Wystarczy:

1. Tania ładowarka z Wi-Fi i protokołem Tuya
2. Home Assistant jako centrum automatyki
3. Biblioteka TinyTuya do lokalnej kontroli
4. Trochę Pythona w AppDaemon

Efekt: auto ładuje się za darmo gdy świeci słońce, a przy ujemnych cenach Pstryk — operator energii dopłaca za to, że pobieramy prąd.

Latem planujemy naładować całą baterię 75 kWh praktycznie bez kosztów. Policzymy to jesienią.

---

*Artykuł napisany na podstawie rzeczywistej instalacji, maj 2026.*
