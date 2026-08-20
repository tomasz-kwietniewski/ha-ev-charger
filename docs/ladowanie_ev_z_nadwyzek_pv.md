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

## Logika sterowania — sześć trybów

Skrypt AppDaemon co 30 sekund sprawdza stan instalacji i podejmuje decyzję. W aktualnej wersji obsługuje sześć trybów pracy:

| Tryb                 | Warunek                                     | Działanie                                                  |
| -------------------- | ------------------------------------------- | ----------------------------------------------------------- |
| `EMERGENCY`        | Włączony ręcznie przez toggle w HA       | Ładuj natychmiast na 13A (~9 kW), niezależnie od PV i cen |
| `NEGATIVE_PRICE`   | Cena Pstryk < 0 zł/kWh                     | Ładuj na 13A (~9 kW, bufor ~2 kW na dom)                   |
| `WINTER_NIGHT`     | Tryb zimowy włączony, godz. 22–6         | Ładuj na 10A (tania taryfa nocna)                          |
| `SOLAR`            | SOC baterii ≥ 95% i nadwyżka PV ≥ 1,6 kW | Ładuj proporcjonalnie do nadwyżki (6–16A)                |
| `BATTERY_PRIORITY` | SOC < 95%                                   | Czekaj, priorytet ładowania baterii                        |
| `IDLE`             | Brak nadwyżek lub auto niepodłączone     | Ładowarka wyłączona                                      |

Tryby sprawdzane są w kolejności od góry — EMERGENCY ma najwyższy priorytet.

### Tryb EMERGENCY — ładowanie awaryjne na maksa

Dodany po tym jak pewnego dnia wróciłem do domu z prawie pustą baterią auta i za godzinę musiałem jechać znowu. Słońca było mało, a skrypt solarny czekał na nadwyżki.

Rozwiązanie: przełącznik w dashboardzie HA z timerem. Ustawiasz ile godzin (0,5–8h), włączasz toggle — ładowarka rusza natychmiast na 13A (~9 kW). Nie czeka na słońce, może drenować magazyn (ale zatrzyma się gdy SOC baterii spadnie poniżej 20%). Po upływie czasu automatycznie wraca do trybu normalnego.

```python
EMERGENCY_CURRENT_A = 13   # zostawia ~2 kW bufora na dom przy przyłączu 11 kW
SOC_EMERGENCY_MIN   = 20   # nie drenuj magazynu poniżej 20%
```

### Znak PCC Sofara — weryfikuj empirycznie

To jedna z ważniejszych pułapek. Sensor `sensor.sofar_modbus_inverter_active_power_pcc_total` może mieć różny znak w zależności od wersji firmware i trybu pracy falownika. W mojej instalacji:

- **Dodatni PCC** = eksport do sieci (nadwyżka)
- **Ujemny PCC** = import z sieci (brak nadwyżki)

Sprawdź w Developer Tools wartość tego sensora gdy wiesz że eksportujesz (bateria pełna, słońce świeci). Jeśli wartość jest ujemna przy eksporcie — zamień znak w kodzie.

### Nadwyżka to nie samo PCC

Naturalny odruch: „nadwyżka = to, co wypycham do sieci", czyli PCC. Przy falowniku hybrydowym to jednak zły sygnał sterujący, bo Sofar w trybie self-use **aktywnie kompensuje deficyt z magazynu** i trzyma PCC blisko zera. Przy PV 1 kW, domu 5 kW i ładowarce ciągnącej 4 kW licznik pokaże PCC ≈ 0 — a skrypt uzna, że jest w równowadze, choć realnie opróżnia baterię domową.

Dlatego nadwyżkę liczę jako minimum z dwóch sygnałów:

```python
surplus_without_ev_kw = min(grid_power, pv_power - load_power)
```

`PV − dom` widzi deficyt maskowany przez magazyn. PCC z kolei pilnuje, żeby nie zabrać mocy, którą falownik akurat wpompowuje do baterii (SOC 95-99%, `PV − dom` jest wtedy większe niż realny eksport). Minimum z obu to nadwyżka, którą można wziąć bez szkody dla magazynu.

### Uśrednianie — eliminacja migotania

Sygnał "migocze" — raz -0,1 kW, raz +0,2 kW, raz -0,5 kW — nawet gdy bilans jest w zasadzie zero. To normalne przy hybrydowym falowniku, regulacja nie jest idealna. Bez filtrowania skrypt zmieniałby prąd ładowania co 30 sekund.

Rozwiązanie: uśrednianie z ostatnich 3 odczytów (90 sekund):

```python
PCC_HISTORY_SIZE = 3

# uwaga: do historii trafia wartość PO doliczeniu poboru ładowarki,
# żeby każda próbka znaczyła to samo (patrz Problem 19)
self._surplus_history.append(available_kw)
if len(self._surplus_history) > PCC_HISTORY_SIZE:
    self._surplus_history.pop(0)
avg_available_kw = sum(self._surplus_history) / len(self._surplus_history)
```

### Bias +1000W — agresywne wykorzystanie nadwyżek

Prąd ładowarki zmienia się skokowo co 690W (1A × 3 fazy × 230V). Żeby skrypt był trochę bardziej "agresywny" i częściej wybierał wyższy prąd, dodałem stały bias +1000W do obliczonej nadwyżki. Dzięki temu auto startuje już przy ~0,6 kW realnego eksportu zamiast czekać na pełne 1,6 kW. W kodzie bias jest wydzielony jako nazwana stała:

```python
SURPLUS_BIAS_W = 1000  # bufor zachęcający do startu

# Bez podłogi — przy imporcie wychodzi ujemne i regulacja redukuje prąd
surplus_w = avg_surplus_kw * 1000 + SURPLUS_BIAS_W
```

Ważne, żeby nie zastępować ujemnej nadwyżki stałą wartością biasu — pierwotna wersja tak robiła i sterowanie „uciekało" w górę przy zachmurzeniu (Problem 19).

Przy cenie 0,15 zł/kWh to koszt ~15 groszy za godzinę ładowania w zamian za lepsze wykorzystanie słońca. Latem przy cenach bliskich zeru — bez znaczenia.

### Stan PAUSE — ładowarka gotowa ale wstrzymana

Gdy auto jest podłączone ale ładowanie jest wstrzymane (np. przez harmonogram), ładowarka raportuje stan `PAUSE`. Stary kod nie obsługiwał tego stanu i nie wysyłał START — auto stało podłączone ale się nie ładowało.

Rozwiązanie: traktuj `PAUSE` jak `IDLE` — auto jest gotowe do ładowania:

```python
CHARGER_READY_STATES   = {"PAUSE", "SLEEP", "IDLE", "UNKNOWN"}
CHARGER_WORKING_STATES = {"WORKING"}
```

---

## Pułapki techniczne — kompletna lista

### Problem 1: Protokół Tuya 3.5

Local Tuya obsługuje tylko do wersji 3.4. Jedyne rozwiązanie: AppDaemon + TinyTuya.

### Problem 2: Klucze DP jako stringi

```python
dps.get("109")  # poprawnie
dps.get(109)    # zawsze None
```

### Problem 3: DP 151 blokuje START

Ładowarka ma wbudowany harmonogram (DP 151). Gdy harmonogram jest aktywny, ładowarka ignoruje zewnętrzne komendy START i pozostaje w PAUSE. Rozwiązanie — wyczyść harmonogram przy każdym starcie:

```python
self._device.set_value("151", json.dumps({"m":0,"dt":0,"ss":"00:00","se":"00:00"}))
```

### Problem 4: Znak PCC zmienia się po zmianie trybu Sofara

Po zmianie trybu falownika (np. z Self-use na Time of Use) znak PCC może się odwrócić. Zawsze weryfikuj empirycznie po każdej zmianie konfiguracji falownika.

### Problem 5: Moc DP 102 mnożona x100

`L1[2]`, `L2[2]`, `L3[2]` to moc per faza w jednostkach x100W. Wartość `32` oznacza 3200W, nie 32W.

### Problem 6: Stan PAUSE ignorowany

Gdy auto podłączone ale harmonogram wstrzymał ładowanie — ładowarka raportuje PAUSE. Stary kod nie wysyłał START w tym stanie.

### Problem 7: Uśrednianie PCC konieczne

Bez filtrowania migające wartości PCC powodują chaotyczne zmiany prądu co 30 sekund.

### Problem 8: Próg startu za wysoki

Pierwotny próg START_SURPLUS_W = 5000W był za wysoki — system nie startował przy nadwyżkach 3–4 kW. Aktualny próg: 1600W (razem z biasem 1000W to znaczy, że auto startuje już przy ~0,6 kW realnego eksportu PCC).

### Problem 9: Serwery Tuya dla Polski

Dla europejskich użytkowników dane trafiają na serwer w **Frankfurcie** (AWS). Nie w Chinach. To ważne przy konfiguracji Tuya IoT Platform — wybierz region "Central Europe".

### Problem 10: Helpery tylko przez UI

Encje zdefiniowane w YAML są read-only dla serwisów HA. Twórz helpery wyłącznie przez UI (Settings → Helpers → Add).

### Problem 11: Nie twórz sensorów przez AppDaemon set_state()

W HA 2026.x API odrzuca encje z atrybutami `unit_of_measurement` i `device_class` tworzonymi przez AppDaemon. Używaj `input_text` jako pośrednika i template sensorów w `configuration.yaml`.

### Problem 12: AppDaemon skanuje folder apps/ rekurencyjnie

AppDaemon ładuje wszystkie pliki `.yaml` z folderu `apps/` — łącznie z podfolderami. Jeśli wewnątrz `apps/` umieścisz backup z poprzednim `apps.yaml`, AppDaemon załaduje go razem z aktualnym i uruchomi duplikaty wszystkich aplikacji.

W praktyce wygląda to tak: masz jeden skrypt sterujący ładowarką, a działają dwie instancje — każda wysyła komendy do ładowarki co 30 sekund, wzajemnie sobie przeszkadzając. W logach zobaczysz dwa razy `Calling initialize() for ev_charger_*` przy starcie.

Rozwiązanie: trzymaj backupy **poza** folderem `apps/`, np. w `addon_configs/a0d7b954_appdaemon/_backups/`.

### Problem 13: STOP-spam w gałęzi IDLE

W `_apply_decision` gałąź `BATTERY_PRIORITY/IDLE/OFFLINE` początkowo nie miała guardu sprawdzającego ostatnio wysłany switch — co iterację (30 s) wysyłała komendę STOP do ładowarki, nawet jeśli już wcześniej została wysłana. Skutek dwojaki: niepotrzebne pakiety przez sieć do wallboxa **oraz** słyszalne klikanie stycznika ładowarki — każdy STOP wymusza cykl przekaźnika.

W logach widać było po kilkanaście STOPów pod rząd w 8 minutach mimo, że stan logiczny się nie zmieniał.

Rozwiązanie: dodać guard `if self._last_sent_switch != False:` analogicznie do gałęzi SOLAR (gdzie analogiczny guard `!= True` już był). Dzięki temu STOP idzie raz na przejście z aktywnego trybu w IDLE, a nie co iterację.

### Problem 14: DP 102 potrafi się „zamrozić" — firmware quirk dé EV v2.9.4

Najbardziej podstępny problem jaki spotkałem. Firmware wallboxa dé EV (sprawdzane na wersji **2.9.4**) potrafi zamrozić cały blok pomiarów w DP 102 — wartości napięć, prądów, mocy, energii sesji i temperatury (`L1`/`L2`/`L3`/`p`/`e`/`t`) zwracają identyczny string przez wiele godzin, mimo że auto fizycznie się ładuje. DP 109 (status) nadal raportuje poprawnie `WORKING`, sterowanie (DP 140 switch, DP 150 current) działa, **tylko pomiary kłamią**.

Diagnoza tego trwała godziny. Wprowadzało w błąd, że Smart Life cyklicznie pokazywało „Charging"/„Paused" — sugerując że to wallbox cyklicznie zatrzymuje sesję. W rzeczywistości auto ładowało stabilnie ~8 kW, tylko nasz skrypt liczył 0 W (a więc też 0 kWh do liczników miesięcznych).

Rozstrzygnięciem był pełny bilans Sofara — porównanie PV, obciążenia, magazynu i PCC — które jednoznacznie pokazało że ~8 kW znika gdzieś (czyli idzie do auta).

**Lekarstwo**: w aplikacji Smart Life otworzyć urządzenie → Settings (zębatka) → **Reboot** (NIE Reset to Factory — to kasuje pairing). Po ~30 sekundach DP 102 wraca do raportowania prawdziwych wartości. Mechanizm — najpewniej zerwany sync wallboxa z chmurą Tuya, soft reboot przywraca.

W skrypcie dodałem **watchdog** który ostrzega w logach (poziom WARNING) gdy w aktywnym trybie ładowania utrzymuje się `status=WORKING + power=0W` przez ponad 10 minut. Próg konfigurowalny przez stałą `WATCHDOG_FROZEN_DP_THRESHOLD`. Skrypt nie restartuje sam — wymagana ręczna interwencja w Smart Life (na razie, do dalszego rozważenia).

> **Uzupełnienie z 11 sierpnia 2026:** zdanie „sterowanie działa, tylko pomiary kłamią" okazało się prawdziwe jedynie dla łagodniejszej postaci tej usterki. Ten sam firmware potrafi zawiesić się głębiej: zamrożone są wtedy również status DP 109 i wykonywanie komend DP 140, choć urządzenie nadal odpowiada w sieci. Pełny opis, sygnatura rozpoznawcza i siedem poprawek w samym skrypcie znajdują się w Problemie 24.

### Problem 15: DP 151 — chmura Tuya potrafi wpychać harmonogram

Po reboocie wallboxa zaobserwowałem że DP 151 (harmonogram) zmienił się z pustego `{"m":0,"dt":0,"ss":"00:00","se":"00:00"}` na `{"m":0,"dt":0,"ss":"15:00","se":"17:00"}` — chmura Tuya wpchnęła resztkowy harmonogram. Pole `m:0` oznacza nieaktywny, więc *w tym przypadku* nie blokuje ładowania, ale daje ślad że chmura może modyfikować wallbox lokalnie bez naszego udziału.

W skrypcie dodałem diagnostykę logującą każdą zmianę DP 151 oraz utrzymuję wywołanie `_clear_schedule()` w `initialize()` AppDaemona (raz po starcie skryptu) i w momencie każdego startu sesji. To zabezpiecza przed sytuacją gdy chmura wpchnie tym razem `m:1` (aktywny harmonogram blokujący).

### Problem 16: DP 102 ma ukryte pole `e` — energia sesji × 0,1 kWh

Przy okazji diagnostyki Problemu 14 odkryłem że DP 102 oprócz `L1`/`L2`/`L3`/`p`/`t` zawiera też pole **`e`** — licznik energii bieżącej sesji w jednostce 0,1 kWh. Po `e:5` minęło 0,5 kWh sesji, przy `e:23` mamy 2,3 kWh. Niezależne od naszego liczenia `power_w × dt`, mniej podatne na błędy zaokrąglenia.

Plus pole `d` w DP 102 to **duration sesji** ale w jakichś własnych jednostkach wallboxa (nie sekundach realnych — przyrosty są nieregularne). Pole `t` to **temperatura ładowarki × 10** (`360` = 36,0 °C).

DP 105 z kolei to **historia ostatniej zakończonej sesji** — JSON z polami `t` (timestamp), `s/e` (start/end HH:MM), `d` (duration w sekundach), `c` (kWh × 10). Dostępne natychmiast po zakończeniu sesji — można nasłuchiwać zmian DP 105 i mieć w HA dokładny licznik sesji niezależny od `power × dt`.

### Problem 17: Archiwum historii miesięcznej — dane ginęły przy resecie

Licznik `_month_energy_kwh` oraz utility_meters zerują się 1. dnia miesiąca. Stary kod logował tylko `Nowy miesiac! Reset: X kWh` i kasował wartość — historia poprzednich miesięcy przepadała, nie dało się porównać miesiąca do miesiąca.

Rozwiązanie ma trzy subtelności warte zapamiętania:

1. **Wyścig z resetem utility_meter.** Gdyby przy przełomie miesiąca odczytać `sensor.produkcja_pv_miesiac` „na bieżąco", można trafić już po jego wyzerowaniu i zapisać ~0. Dlatego skrypt w każdej iteracji zapamiętuje snapshot liczników (`_um_snapshot`), a przy przełomie archiwizuje snapshot z **poprzedniej** iteracji — czyli stan na koniec starego miesiąca. Niezależnie od kolejności resetów.

2. **Fallback po restarcie.** Jeśli AppDaemon wstanie świeżo (pusty snapshot) tuż po przełomie, sięga po atrybut `last_period` liczników utility_meter — HA trzyma tam wartość poprzedniego cyklu.

3. **Trwały znacznik miesiąca.** Zamiast `datetime.now().month` w RAM, miesiąc trzymany jest jako `ev_last_ym` (`"YYYY-MM"`) w pliku persistent. Dzięki temu archiwizacja zadziała nawet, gdy serwer był wyłączony 1. dnia miesiąca i wstał np. 2-go.

Publikacja sensora to osobny temat — patrz Problem 18.

### Problem 18: `set_state()` na `sensor.*` zwraca 400 w HA 2026.x — publikacja przez REST API

Pierwsza wersja archiwum publikowała `sensor.ev_historia_miesieczna` przez AppDaemon `set_state()`. Na HA **2026.6.1** + AppDaemon **4.5.13** kończyło się to w logach błędem:

```
ERROR HASS: [400] HTTP POST: Bad Request {'attributes': {'friendly_name': ..., 'months': []}}
ERROR HASS: Error setting state: Bad Request
```

To rozwinięcie Problemu 11. Wbrew pierwotnej hipotezie **nie chodzi tylko o `unit_of_measurement`/`device_class`** — `set_state()` na encji `sensor.*` zwraca 400 nawet z gołymi atrybutami. Diagnostyka empiryczna (POST wprost do REST API rdzenia przez proxy supervisora, `http://supervisor/core/api/states/...` z `$SUPERVISOR_TOKEN`) pokazała, że **samo REST API przyjmuje identyczny payload bez zająknięcia (HTTP 201)** — ze stanem `int` i `string`, z `months`, `friendly_name`, `icon`. Wina leży więc po stronie ścieżki `set_state()` w tej wersji AppDaemona, nie HA.

Dlaczego nie `input_text` + template (sprawdzony wzorzec z Problemu 11)? Bo archiwum (do 120 miesięcy / 10 lat × 7 pól) nie zmieści się w `input_text` (limit 255 znaków) ani w stanie encji (też 255). Atrybuty encji limitu nie mają.

Rozwiązanie: `_publish_history()` robi bezpośredni `requests.post(...)` do REST API rdzenia z tokenem z `os.environ["SUPERVISOR_TOKEN"]` (addon ma `homeassistant_api: true`, więc token i proxy są dostępne). Całe archiwum siedzi w atrybucie `months`; stan = kWh ostatniego miesiąca; jednostki opisują karty dashboardu. Źródłem prawdy pozostaje plik `ev_charger_data.json` (klucz `ev_history`) — encja to tylko warstwa prezentacji, odtwarzana przy każdym `initialize()` (czyli też po restarcie HA, gdy AppDaemon przełącza połączenie).

### Problem 19: Regulacja SOLAR „uciekała w górę" przy zachmurzeniu

Najpoważniejszy błąd wykryty podczas audytu kodu — nigdy nie zdiagnozowany z logów, bo objawiał się jako „auto ładuje się mocniej niż powinno, a potem nagle stop".

Pierwotny kod liczył nadwyżkę tak: jeśli PCC pokazuje eksport, `surplus = PCC + 1000 W`; jeśli import — `surplus = 1000 W`. Ta druga gałąź wyrzucała informację **jak duży** jest import. W trybie SOLAR do nadwyżki dolicza się jeszcze moc ładowarki (bo jej pobór siedzi już w zużyciu domu), więc przy deficycie wychodziło:

```
available = 1000 W + moc_ladowarki
target    = int(available / 690)
```

To jest dodatnie sprzężenie zwrotne. Ładowarka na 6A (4,1 kW) daje `target = int(5140/690) = 7A`, przy 7A (4,8 kW) wychodzi `8A`, i tak dalej aż do 16A — mimo że słońce zaszło za chmurę i energia leci z magazynu domowego. Warunek STOP (`available < 1200 W`) wymagałby mocy ładowarki poniżej 200 W, czyli w praktyce nie zadziała nigdy. Ładowanie kończyło się dopiero, gdy SOC magazynu spadł poniżej 95% i wszedł `BATTERY_PRIORITY` — czyli po niepotrzebnym cyklu rozładowania baterii.

Drugi, subtelniejszy problem: **samo PCC to zły sygnał sterujący dla instalacji hybrydowej**. Sofar w trybie self-use kompensuje deficyt z magazynu i trzyma PCC blisko zera — przy PV 1 kW, domu 5 kW i ładowarce na 6A licznik pokaże PCC ≈ 0, choć realnie 4 kW idzie z baterii.

Rozwiązanie łączy oba wnioski:

```python
# min() bierze wariant konserwatywny: nadwyżka dostępna bez ruszania magazynu
surplus_without_ev_kw = min(grid_power, pv_power - load_power)
# pobór auta siedzi już w load_power — doliczamy go z powrotem
available_kw = surplus_without_ev_kw + charger_power_kw
# ... uśrednianie 3 próbek ...
surplus_w = avg_available_kw * 1000 + SURPLUS_BIAS_W   # BEZ podłogi — może być ujemne
```

`PV − dom` widzi deficyt maskowany przez magazyn; PCC z kolei pilnuje, żeby nie zabrać mocy, którą falownik akurat ładuje do baterii (SOC 95-99%). Minimum z obu to nadwyżka, którą można wziąć bez szkody dla magazynu. Brak podłogi sprawia, że przy imporcie `surplus_w` schodzi poniżej zera i regulacja realnie redukuje prąd, a histereza STOP wreszcie działa.

**Kolejność operacji okazała się równie ważna, co sam wzór.** Pierwsza wersja poprawki uśredniała nadwyżkę, a moc ładowarki dodawała dopiero w `_decide`. Symulacja pokazała, że to wciąż daje skok prądu: w pierwszej iteracji po starcie sesji historia zawiera próbkę zmierzoną przy wyłączonej ładowarce i próbkę zmierzoną przy 7,6 kW poboru — dodanie do takiej średniej bieżącej mocy ładowarki liczy ten pobór półtora raza. Efekt: przy PV 8 kW skrypt skakał na 16A (11 kW). Kompensacja musi iść **przed** uśrednianiem, żeby każda próbka w historii znaczyła to samo — „ile w tej chwili jest do dyspozycji dla auta". Wtedy uśrednianie robi to, do czego służy (tłumi migotanie), zamiast wprowadzać opóźnienie w pętli sprzężenia zwrotnego.

Jest jeszcze jeden przypadek brzegowy, który wychodzi dopiero przy połączeniu tej zmiany z Problemem 14: gdy DP 102 zamarznie, ładowarka raportuje `WORKING` i 0 W, więc kompensacja wychodzi zerowa, a `PV − dom` pokazuje ogromny deficyt (auto realnie ciągnie 8 kW). Skrypt uznałby to za brak nadwyżki i przerwał realnie trwającą sesję — zamiast pozwolić watchdogowi dojść do progu i ostrzec. Dlatego po dwóch iteracjach `WORKING + 0 W` do kompensacji podstawiana jest ostatnia znana moc (`FROZEN_DP_FALLBACK_ITERS`). Dwie iteracje, bo tuż po starcie sesji chwilowe zero jest normalne — auto negocjuje z wallboxem.

### Problem 20: Dedup komend bez ponowień = zakleszczenie sterowania

Fix na STOP-spam (Problem 13) wprowadził pole `_last_sent_switch` — „nie wysyłaj drugi raz tego samego". Rozwiązał klikanie stycznika, ale wprowadził cichą regresję: `_set_switch()` łykał wyjątek sieciowy, a `_last_sent_switch` i tak zapisywało się na `True`. Jeden zgubiony pakiet w Wi-Fi (a wallbox stoi w garażu, zasięg bywa marny) i skrypt do końca życia procesu uważał, że START został wysłany. Ładowanie nie ruszało aż do restartu AppDaemona albo przełączenia trybu awaryjnego. W drugą stronę było gorzej — nieudany STOP oznaczał ładowanie mimo `BATTERY_PRIORITY`.

Poprawka ma dwie warstwy:

1. `_set_switch()` / `_set_current()` zwracają `True/False`, a `_last_sent_*` aktualizuje się **tylko przy udanej wysyłce**. Nieudana komenda jest ponawiana w następnej iteracji (30 s).
2. Osobno obsłużony przypadek „komenda poszła, ale wallbox jej nie wykonał": licznik niezgodności między intencją a stanem DP 109. Po `SWITCH_RETRY_ITERATIONS` (4 iteracje = 2 min) komenda idzie ponownie. START ma limit 3 ponowień (auto może być po prostu naładowane do 100% i nie przyjmie sesji — nie ma sensu walić w nie w nieskończoność), STOP ponawiany jest bez limitu, bo to kwestia bezpieczeństwa.

### Problem 21: TinyTuya zwraca błąd jako dict, nie wyjątek

`device.status()` przy problemach sieciowych często **nie rzuca wyjątku**, tylko zwraca `{"Error": "Network Error", "Err": "901"}`. Stary kod robił `raw.get("dps", {})` — dostawał pusty słownik, status wychodził `"UNKNOWN"`, a `UNKNOWN` jest w `CHARGER_READY_STATES`. Efekt: ładowarka uznana za online i gotową do ładowania, `online: True`, komendy wysyłane w próżnię (i, przed Problemem 20, zakleszczenie sterowania).

Poprawka: sprawdzamy `"Error" in raw` i traktujemy taką odpowiedź jak brak łączności. Przy okazji `set_socketRetryLimit` zszedł z 3 na 1 — trzy próby po 6 s timeoutu plus drugi odczyt potrafiły zablokować wątek AppDaemona na ~36 s, czyli dłużej niż interwał pętli.

### Problem 22: Nieatomowy zapis pliku persistent

`ev_charger_data.json` trzyma liczniki energii i całe 10-letnie archiwum, a zapisywany był w miejscu (`open(path, "w")` + `json.dump`). Przerwanie w trakcie zapisu (restart add-onu, brak miejsca) zostawia obcięty JSON. Gorzej: od tego momentu **każdy** kolejny `_save_persistent` padał na `json.load` w fazie read-modify-write, a `_load_persistent` cicho zwracał wartości domyślne. Liczniki wyzerowane, archiwum niedostępne, w logach tylko WARNING co 30 s.

Poprawka: zapis do pliku tymczasowego i `os.replace()` (atomowy na tym samym systemie plików), a nieczytelny plik jest odkładany jako `.corrupt` i skrypt startuje od pustego stanu zamiast zapętlać się na błędzie. Zapisy `ev_month_energy_kwh` i `ev_total_energy_kwh` zostały scalone w jeden read-modify-write zamiast dwóch na iterację.

### Problem 23: Regulacja goniąca szum — ładowarka pikająca co 30 sekund

Ten problem zgłosiło ucho, nie log. Pierwszego dnia po naprawie regulacji usłyszałem przez okno, że wallbox pika bardzo często. Logi potwierdziły od razu: w ciągu trzech minut prąd zmienił się pięć razy, w tym sekwencja **10A → 11A → 10A w ciągu 60 sekund**.

Przyczyny są dwie i obie są pouczające.

**Brak strefy nieczułości.** Komenda szła do ładowarki, ilekroć nowy cel różnił się od poprzedniego choćby o 1 A:

```python
if target_current > 0 and target_current != self._last_sent_current:
```

Jeden amper to jednak tylko 690 W (3 fazy × 230 V), a `int()` obcina wynik dzielenia. Gdy nadwyżka stanęła dokładnie na granicy stopnia, wystarczyło wahanie rzędu **±30 W**, czyli ułamka procenta, żeby cel przeskakiwał w każdej iteracji. W symulacji „stabilnego słońca" z takim właśnie szumem stary kod wygenerował 29 zmian prądu w 15 minut.

**Pętla szybsza niż obiekt, którym steruje.** To poważniejsza sprawa. Porównanie celu z rzeczywistą mocą pokazało, że auto dochodzi do zadanego prądu z opóźnieniem około minuty: przy celu 10A moc odpowiadała najpierw 8,5A, minutę później 9,4A. Ponieważ zmierzony pobór ładowarki wraca do wyliczenia nadwyżki, sterownik reagował na stan, który jeszcze się nie ustalił, i sam sobie generował oscylacje. Klasyczny błąd w regulacji ze sprzężeniem zwrotnym.

Rozwiązanie ma dwie warstwy. **Histereza ±250 W wokół progu stopnia**: żeby podnieść prąd, nadwyżka musi przekroczyć próg z zapasem, i tak samo w drugą stronę. To samo w sobie zabija przeskoki na granicy. **Potwierdzenie zmiany w czasie**: nowy cel musi utrzymać się przez 2 iteracje (60 s), zanim komenda pójdzie do wallboxa.

Wyjątkiem jest spadek o 3 A lub więcej — ten idzie natychmiast, bo chroni przyłącze 11 kW, gdy nagle ruszy pompa ciepła albo piekarnik.

Świadomie wygładzam też **małe** redukcje, choć pierwszy szkic poprawki miał je wykonywać od ręki. Zmieniłem zdanie po prostej refleksji: krótkie zejście w magazyn domowy nie jest tragedią, bo magazyn i tak się doładuje, gdy słońce wyjdzie zza chmury. Rzadsze szarpanie ładowarką jest tego warte.

Efekt zmierzony na symulacji, w tym na realnych nadwyżkach z logów:

| Scenariusz | Przed | Po |
| --- | --- | --- |
| Realne logi (3 minuty) | 5 zmian | 1 |
| Pochmurne 30 minut | 52 zmiany | 1 |
| Stabilne słońce 15 minut | 29 zmian | 0 |

Koszt wolniejszego podbijania mocy to 0,04-0,09 kWh, czyli kilka groszy. Weryfikacja na produkcji potwierdziła rzecz jeszcze ładniejszą: w oknie 2,5 minuty PV spadło chwilowo z 6,8 kW do 1,6 kW (przelotna chmura), a prąd **nie zmienił się ani razu** — dołek nie utrzymał się przez wymagane dwie iteracje, więc sterownik go zignorował i słońce wróciło.

**Wniosek ogólny:** przy sterowaniu z pętlą zwrotną nie wystarczy poprawnie policzyć wartość zadaną. Trzeba jeszcze zapytać, jak szybko obiekt na nią odpowiada, i nie wysyłać komend częściej. Inaczej regulator ściga własny ogon.

---

### Problem 24: Wallbox zawieszony przez 36 godzin, a system tego nie zauważył

Ten problem zgłosiłem ja, patrząc na wykres przepływów. Cztery kilowaty nadwyżki szły do sieci, auto stało podłączone, a na jego desce widniało „ładowanie zakończone". Skrypt w tym czasie pracował z pozoru wzorowo: liczył nadwyżkę, trzymał tryb SOLAR, regulował prąd do 9A, potem do 7A, potem do 8A.

Diagnoza zajęła kilkanaście minut i sprowadziła się do jednego pytania: **czy dane, które czytamy, w ogóle są świeże?**

Watchdog raportował „prawdopodobne zamrożenie DP 102" cztery razy w ciągu 36 godzin. Za każdym razem dołączał surowy odczyt. Wszystkie cztery były identyczne co do bitu:

```
{"L1":[2430,0,0],"L2":[2430,0,0],"L3":[2430,0,0],"t":330,"p":0,"d":0,"e":0}
```

Napięcie 243,0 V dokładnie takie samo na trzech fazach i temperatura obudowy niezmienna od poprzedniego dnia. Realny pomiar tak nie wygląda: napięcie w sieci drga o kilka woltów w każdej minucie, a trzy fazy nigdy nie mają identycznej wartości. To nie było „auto nie chce ładować" tylko martwy blok danych.

Reszta obrazu pasowała do zawieszenia firmware, nie do problemu z autem:

- **komendy ignorowane w obie strony** - cztery START-y poprzedniego dnia przy statusie `IDLE` i cztery STOP-y następnego przy `WORKING`, wszystkie bez najmniejszej reakcji;
- **641 kolejnych odczytów statusu `WORKING`** bez jednej zmiany mocy;
- **ani jednej iteracji z mocą powyżej zera przez 36 godzin**, licznik sesji na 0,000 kWh;
- przy tym zero błędów łączności, a ping do wallboxa wracał w 3 ms.

Urządzenie odpowiadało w sieci, tylko jego warstwa aplikacyjna stała. Auto pokazywało „ładowanie zakończone", bo wallbox przestał podawać PWM na Control Pilot i sesja została zamknięta.

Lekarstwo okazało się takie samo jak przy Problemie 14: **Reboot z aplikacji Smart Life** (Settings, nie Reset to Factory). Moc skoczyła z 0 na 3700 W w niecałą minutę, przy zupełnie niezmienionym kodzie. Ślad restartu widać w logach po tym, że chmura Tuya wpycha wtedy swój harmonogram na DP 151 - dokładnie tak, jak opisuje Problem 15.

**Ale najciekawsze jest to, co ta awaria powiedziała o samym skrypcie.** Wallbox zawiesił się z powodu firmware i na to nie mam wpływu. To, że nikt się o tym nie dowiedział przez półtorej doby przy dwóch słonecznych dniach, było już winą kodu. Znalazłem siedem osobnych usterek, wszystkie z tej samej rodziny: **system nie odróżniał „urządzenie milczy" od „urządzenie mówi, że wszystko gra".**

1. **Watchdog diagnozował po najsłabszym możliwym sygnale.** Patrzył na `power_w == 0`, co wygląda identycznie przy awarii i przy aucie, które jest po prostu naładowane. Mocniejsza sygnatura leżała w danych przez cały czas: niezmienny surowy DP 102. Teraz porównujemy właśnie ten ciąg, a najczystszym wskaźnikiem jest pole `t` z temperaturą, bo ono drga zawsze. Czas wykrycia spadł z „nigdy" do pięciu minut.

2. **Licznik gubił się przy migotaniu trybu.** Streak liczył się tylko w trybie aktywnym, więc każde zejście do IDLE kasowało go do zera. Przy nadwyżce stojącej na granicy progu tryb przerzuca się co kilka iteracji, i licznik wyzerował się w połowie awarii przy stanie 40. Ten sam błąd podcinał fallback kompensacji, który bez streaka przestawał podstawiać ostatnią znaną moc, przez co skrypt widział wielki deficyt i STOP-ował realnie trwającą sesję. Jedna poprawka naprawiła oba.

3. **Jedyną reakcją na awarię był WARNING w logu.** Teraz powstaje powiadomienie w Home Assistant, jedno na epizod, kasowane automatycznie po powrocie wallboxa do pracy.

4. **START i STOP miały rozłączne liczniki i żaden nie łączył kropek.** Osobno mieściły się w swoich limitach i milkły, choć razem opowiadały jedną historię: to urządzenie nie wykonuje niczego, o co je prosimy.

5. **„Odpuszczam do zmiany warunków" znaczyło w praktyce „do jutra".** Po trzech nieudanych próbach startu skrypt zamilkł, a licznik prób resetował się wyłącznie w gałęzi nieaktywnego trybu, w którą przy trwałej nadwyżce się nie wchodzi. Efekt: cisza od 10:30 do końca dnia, przy nadwyżce sięgającej 8,6 kW. Teraz po 30 minutach wraca kolejna seria prób, a zmiana statusu ładowarki resetuje liczniki od razu.

6. **Przy statusie `WORKING` skrypt nie miał żadnej ścieżki wznowienia sesji.** Warunek `if not charger_working` nigdy nie był prawdziwy, więc START nie mógł pójść z definicji. To boli podwójnie, bo auta Stellantisa po zatrzymaniu ładowania zamykają sesję i same jej nie wznawiają. Doszedł cykl STOP i START po 15 minutach bez poboru, z twardym limitem dwóch prób, żeby nie wrócić do klikania stycznikiem z Problemu 13.

7. **Zadany prąd szedł na ślepo.** DP 150 mówi, jaki prąd wallbox faktycznie ma ustawiony, i był czytany, ale nigdy porównywany z tym, co wysłaliśmy. Skrypt posłał 6A, 9A, 7A i 8A do martwego urządzenia i każdą komendę uznał za sukces, bo `set_value` nie zgłosił wyjątku.

**Wniosek ogólny, i to chyba najważniejszy z całego projektu:** status z urządzenia to deklaracja, nie fakt. `WORKING` znaczy tylko tyle, że wallbox tak twierdzi. Dopóki nie skonfrontuje się tej deklaracji z niezależnym pomiarem - świeżością danych, sprzężeniem zwrotnym z komendy, realnym przepływem mocy - sterownik może godzinami wykonywać precyzyjne obliczenia na martwym obiekcie i nie mieć o tym pojęcia. Warto zapytać nie tylko „co urządzenie mówi", ale też „kiedy ostatnio powiedziało coś nowego".

---

### Problem 25: Alarm, którego nikt nie ogląda, nie jest alarmem

Tydzień po naprawieniu Problemu 24 ta sama awaria wróciła. I tym razem wykrywanie zadziałało wzorowo: watchdog rozpoznał zamrożony DP 102 po pięciu minutach i alarmował **sześć razy** - 18 sierpnia o 13:11, 13:29, 13:58, 14:45 i 15:16, a następnego dnia o 9:51. Każdy alarm z surowym odczytem, każdy z gotową instrukcją co kliknąć.

Awaria trwała **22,5 godziny**. Zmarnowane minimum 12,2 kWh nadwyżki, licząc tylko te próbki, w których skrypt widział realny eksport. Rankiem drugiego dnia do sieci szło 7-9 kW, a ładowarka stała.

Dlaczego? Bo powiadomienie trafiało do panelu Home Assistanta, a do panelu nikt nie zagląda w środku dnia roboczego. Diagnostyka była bez zarzutu, adresat nie istniał.

**Pierwszy wniosek jest banalny i dlatego łatwo go przegapić:** wykrycie awarii ma wartość dopiero wtedy, gdy dociera tam, gdzie człowiek naprawdę patrzy. Alarm poszedł więc dodatkowo pushem na telefon. Panel został jako drugi kanał, nie jedyny.

**Drugi wniosek jest ciekawszy.** Skoro lekarstwo jest znane od maja, zawsze to samo i całkowicie mechaniczne - Reboot z aplikacji - to dlaczego w ogóle czeka na człowieka? Skrypt wie o awarii pięć minut po jej wystąpieniu, a jedyne, co potrafi, to poprosić o kliknięcie. Stąd mechanizm automatycznego restartu: reaktywnie na wykrytą sygnaturę, z limitem trzech prób i dziesięciominutowym odstępem (dłuższym niż okno detekcji, żeby zdążyć ocenić, czy poprzedni pomógł), plus profilaktycznie raz na dobę o czwartej rano, wstrzymany, gdy realnie płynie prąd.

Warto zauważyć, że pierwotny pomysł brzmiał „restartujmy co rano" i nie wystarczyłby: tamta awaria zaczęła się w środku dnia, więc poranny restart uratowałby dokładnie nic z popołudnia. Profilaktyka jest higieną, nie mechanizmem ratunkowym.

**I tu zaczyna się część, która się nie udała.** Okazało się, że wysłanie komendy restartu wcale nie jest trywialne.

Protokół lokalny TinyTuya w ogóle nie zna komendy „reboot" - sprawdzone w źródłach i dokumentacji. Musi to więc być zwykły punkt danych. Podłączyłem się do wallboxa nasłuchem odpytującym co 0,7 sekundy i poprosiłem o kliknięcie Reboot w aplikacji. Wynik był zaskakujący na trzy sposoby:

- **żaden z czternastu widocznych punktów danych nie drgnął** jako komenda;
- **wallbox ani na moment nie zniknął z sieci** - moduł WiFi pracował nieprzerwanie, co znaczy, że „Reboot" resetuje wyłącznie moduł mocy, nie całe urządzenie;
- za to widać było jego skutek: **pole `cp` w DP 106 spadło z 11,7 V do 0,0 V i wróciło po trzech sekundach.**

Przy okazji wyszło, że `cp` to wcale nie „wersja Control Pilot", jak zapisałem w mapie punktów danych w sierpniu, tylko **napięcie Control Pilot w woltach**: około 12 V gdy auto jest odpięte, 9 V gdy podłączone, 6 V podczas ładowania - dokładnie stany A, B i C z normy IEC 61851. Wallbox przez cały czas raportuje, czy kabel siedzi w aucie, a skrypt tego nie czytał.

Skoro komendy nie widać w odczycie, musi być **tylko do zapisu**. I to nie jest teoria: nasz DP 140, którym od miesięcy sterujemy ładowaniem, też nie pojawia się w żadnym odczycie ani skanie - a działa bez zarzutu. Takich punktów po prostu nie da się podsłuchać.

Pozostawało zapytać chmurę Tuya, która zna pełny model urządzenia razem z punktami do zapisu. Tu trafiłem na mur: subskrypcja IoT Core wygasła, a trial jest jednorazowy na konto.

Zostało sprawdzanie kandydatów na żywym sprzęcie. Projekt tuya-local opisuje dla tego produktu przycisk `class: restart` na **DP 142**, a model producenta z chmury potwierdził jego nazwę: `x_do_reboot`. Hipoteza była więc dobra. Gorzej z jej sprawdzeniem.

Za pierwszym razem nic. Za drugim, po wysłaniu pełnego zbocza `False → True`, napięcie Control Pilot spadło do zera dokładnie sekundę po komendzie - czyli **zadziałało**. I to był jedyny taki przypadek. Pięć kolejnych prób, w tym po sześciu minutach przerwy i tuż po ręcznym reboocie z aplikacji, nie dało nic.

Jeden sukces na sześć prób to nie jest mechanizm, tylko anegdota. Automat ma ratować mnie przed 22-godzinną awarią, więc nie może opierać się na komendzie, która działa raz na kilka razy w okolicznościach, których nie umiem odtworzyć. **DP 188** okazał się przy okazji działającym przyciskiem „Refresh" - w odpowiedzi przysłał komplet danych - ale restartu nie robi.

**I tu przyszła odpowiedź, dlaczego nic z tego nie działało.** Okazało się, że w Home Assistancie mam integrację Xtend Tuya, a ta wystawia akcję `xtend_tuya.call_api` - pozwala odpytać chmurę Tuya **kanałem aplikacyjnym**, czyli tym samym, którym posługuje się Smart Life. To ważne rozróżnienie: przez cały dzień wysyłałem nazwę `x_do_reboot` wziętą z modelu **deweloperskiego** z iot.tuya.com, nie sprawdzając, czy kanał aplikacyjny w ogóle nazywa tę komendę tak samo.

Zapytałem więc wprost o specyfikację urządzenia. Odpowiedź:

```
/v1.1/m/life/{id}/specifications  ->  {"category": "", "functions": [], "status": []}
/v1.0/m/life/devices/{id}/status  ->  {"category": "dj", "dpStatusRelationDTOS": []}
```

**Chmura nie ma modelu tej ładowarki.** Zero funkcji, zero relacji punktów danych, a kategoria `dj` oznacza w taksonomii Tuya lampę.

To jedno odkrycie tłumaczy wszystkie niepowodzenia naraz. Xtend Tuya nie utworzyła żadnej encji sterującej, bo nie ma z czego. Komendy po nazwie nie przechodzą, bo żaden kanał nie kojarzy `x_do_reboot` z tym egzemplarzem. A chmura odpowiada `success` i nic nie robi, bo przyjmuje polecenie, którego nie ma komu przekazać. Sprawdziłem jeszcze wariant z numerem punktu zamiast nazwy - `"142"`, także pełnym zboczem - i również nic.

Wniosek jest taki, że **aplikacja Smart Life steruje tym urządzeniem własnym panelem producenta, który omija publiczny model**. Producent dostarcza do aplikacji własny interfejs, a publiczne API o tych funkcjach nic nie wie. Dostępnymi narzędziami tego nie odtworzę i na tym kończę poszukiwania.

Przy okazji drobiazg wart zapamiętania: akcja nazywa się „Call an API **and return the result**", ale wyniku do Home Assistanta **nie zwraca** - wywołanie z `return_response=True` kończy się błędem walidacji. Odpowiedzi API widać dopiero po podniesieniu poziomu logowania integracji do `debug` i zajrzeniu do logu rdzenia. Godzinę patrzyłem na zielony znaczek bez treści, zanim to sprawdziłem.

Warto za to zapamiętać samą zasadę wysyłania takiej komendy: **zboczem, nie wartością**. Punkty tylko do zapisu nie raportują swojego stanu nigdzie - ani lokalnie, ani w chmurze, która pokazuje `x_do_charge` sprzed miesięcy, choć skrypt wysyła na niego START i STOP codziennie. Skoro nie wiadomo, czy punkt nie siedzi już w wartości docelowej, samo wysłanie `true` może być żadną zmianą i przepaść bez śladu.

Jedna rzecz z tego etapu jest warta zapamiętania jako metoda. Pierwszy test uznałem za obiecujący, bo po komendzie wallbox cztery razy przestał odpowiadać, a chmura wepchnęła swój harmonogram - czyli dokładnie to, co widuję po prawdziwym restarcie. Dopiero **próba kontrolna**, czyli identyczny pomiar bez wysyłania czegokolwiek, pokazała, że to był zwykły szum sieciowy. Bez niej wpisałbym do kodu komendę, która nic nie robi, i dowiedziałbym się o tym przy następnej awarii - czyli w najgorszym możliwym momencie. Przy odpytywaniu urządzenia, które ktoś inny też odpytuje, pomiar bez grupy kontrolnej jest wart tyle co nic.

Kolejny kandydat, **DP 141**, w tuya-local nazywa się po prostu „Reset". Może być restartem, a może resetem do ustawień fabrycznych - a wtedy wallbox traci sparowanie i local key przestaje działać, czyli całe sterowanie lokalne pada. Bez działającego dostępu do API, którym ten klucz się odzyskuje, to nie jest ryzyko warte podjęcia. Ten test czeka.

Mechanizm restartu jest więc wdrożony, przetestowany i **śpi**: dopóki kod komendy nie jest znany, zachowuje się dokładnie tak jak przed zmianą, co pilnuje osobny test jednostkowy. Wdrożone i działające od razu są dwie pozostałe rzeczy: push na telefon oraz poprawka znaleziona przypadkiem po drodze.

Ta poprawka to **status `IDLEINS`**. Nasłuch pokazał, że pełna sekwencja startu sesji wygląda tak: `PAUSE → IDLE → IDLEINS → WORKING`, a stan przejściowy trwa około dziewięciu sekund i znaczy „kabel włożony". Nie było go na żadnej liście stanów w kodzie, więc funkcja decyzyjna widziała status spoza wszystkich zbiorów i uznawała, że **auto jest odpięte**. Przy pętli co 30 sekund to od czasu do czasu jedna iteracja bez sterowania - drobiazg. Gorszy jest wariant, w którym wallbox zawiesiłby się właśnie w tym stanie: watchdog by tego nie zobaczył, bo zamrożenie liczy się wyłącznie wtedy, gdy skrypt w ogóle chce ładować. Cicha dziura dokładnie tej samej rodziny co cały Problem 24.

**Wniosek na koniec:** dwie z trzech rzeczy z tej sesji wynikły nie z naprawiania tego, co było zepsute, tylko z **przyglądania się urządzeniu w trakcie normalnej pracy**. Napięcie Control Pilot i stan `IDLEINS` leżały w danych od miesięcy. Wystarczyło raz spojrzeć z rozdzielczością większą niż co trzydzieści sekund.

---

## Helpery w Home Assistant

Wymagane helpery — tworzone przez UI (Settings → Helpers):

| Typ    | Entity ID                            | Opis                                       |
| ------ | ------------------------------------ | ------------------------------------------ |
| Text   | `input_text.ev_charger_status`     | Status ładowarki (WORKING/SLEEP/PAUSE...) |
| Text   | `input_text.ev_charger_mode`       | Aktywny tryb (SOLAR/EMERGENCY...)          |
| Text   | `input_text.ev_data`               | JSON z pełnymi danymi sesji               |
| Toggle | `input_boolean.ev_tryb_zimowy`     | Tryb zimowy — nocne ładowanie 22–6      |
| Toggle | `input_boolean.ev_tryb_awaryjny`   | Tryb awaryjny — ładuj na maksa teraz     |
| Number | `input_number.ev_awaryjny_godziny` | Czas trybu awaryjnego (0,5–8h)            |
| Button | `input_button.ev_archiwizuj_teraz` | Ręczna archiwizacja bieżącego miesiąca (opcjonalny) |

---

## Efekty i wnioski

### Strategia sezonowa — lato i zima

System jest zaprojektowany na cały rok z jednym przełącznikiem sezonowym.

**Lato (kwiecień–wrzesień):**
Polska ma dobre nasłonecznienie — 9 kWp produkuje regularnie nadwyżki powyżej 1,6 kW. Auto ładuje się za darmo z nadwyżek PV. Przy ujemnych cenach Pstryk (które latem zdarzają się regularnie w południe) system ładuje na 13A (~9 kW) — operator energii dopłaca za pobieranie prądu.

**Zima (październik–marzec):**
Krótkie dni, niskie słońce — nadwyżki PV są rzadkie i małe. Jednocześnie od października planowana jest taryfa G12W z tanią energią nocną (~0,70 zł/kWh vs ~0,85 zł/kWh w dzień). Włączam jeden przełącznik w HA — `❄️ Tryb zimowy` — i skrypt automatycznie ładuje auto w nocy między 22:00 a 6:00 na 10A (~6,9 kW).

Dlaczego 10A a nie 16A? Zimą działają pompy ciepła powietrze-powietrze które mogą pobierać łącznie 3–4 kW. Przy przyłączu 11 kW zostaje bezpiecznie ~7 kW na auto, ale przyjąłem 10A (6,9 kW) jako bezpieczny bufor na szczyty poboru (gotowanie, bojler, klimatyzatory).

Słoneczne dni zimą? Skrypt nadal wykrywa nadwyżki PV i uruchamia tryb SOLAR automatycznie — tryb zimowy dodaje tylko nocne okno ładowania, nie wyłącza logiki solarnej.

**Przy ujemnych cenach Pstryk** (które latem zdarzają się regularnie w godzinach 10:00–16:00) system automatycznie ładuje auto na 13A. Pierwotnie brał pełne 16A, ale to 11 kW przy przyłączu 11 kW — bez żadnego zapasu na dom. Teraz zostaje ~2 kW bufora, tak samo jak w trybie awaryjnym. W majowy dzień cena spadła do -0,60 zł/kWh — za każdą godzinę ładowania (9,8 kWh) operator energii **płacił mi** 5,88 zł zamiast żebym ja płacił.

**Ładowanie z nadwyżek** działa dokładnie tak jak planowałem — gdy bateria jest pełna i słońce produkuje więcej niż potrzeba, auto dostaje resztę. Prąd reguluje się co 30 sekund, typowo oscyluje w zakresie 8–12A przy produkcji PV 8 kW.

---

## Historia miesięczna — przeglądanie miesiąc do miesiąca

Przez pierwszych kilka tygodni dashboard pokazywał statystyki tylko z bieżącego miesiąca. Mankament: zarówno wewnętrzny licznik energii naładowanej do auta, jak i miesięczne liczniki `utility_meter` w Home Assistant zerują się 1. dnia każdego miesiąca — a stara wartość trafiała wyłącznie do logu i przepadała. Nie dało się cofnąć w czasie i porównać: ile auto wzięło z PV w maju, a ile w czerwcu.

Dorzuciłem więc trwałe **archiwum miesięczne** z retencją **10 lat**. Tuż przed wyzerowaniem licznika skrypt zapisuje zamknięty miesiąc jako jeden rekord:

- energia naładowana do auta [kWh],
- produkcja PV, zużycie domu, import i eksport z sieci [kWh],
- samowystarczalność energetyczna domu [%].

Przykładowy wpis za czerwiec 2026: **145,86 kWh** wpompowane w auto przy **62,3%** samowystarczalności. Na dashboardzie wyświetlam to jako wykres słupkowy (miesiąc do miesiąca: auto vs produkcja PV vs zużycie domu) oraz tabelę porównawczą.

Diabeł tkwił w dwóch szczegółach, które warto znać:

**Wyścig z resetem.** Gdyby przy przełomie miesiąca odczytać liczniki „na bieżąco", można trafić już po ich wyzerowaniu i zapisać zera. Dlatego skrypt w każdej iteracji (co 30 s) zapamiętuje snapshot liczników, a przy przełomie archiwizuje snapshot z **poprzedniej** iteracji — czyli stan na koniec starego miesiąca. Niezależnie od tego, w jakiej kolejności HA zresetuje `utility_meter`.

**`set_state()` kontra HA 2026.** Pierwsza wersja publikowała sensor archiwum przez AppDaemonowe `set_state()` — i dostawała `400 Bad Request`. Diagnostyka (strzał wprost do REST API rdzenia przez proxy supervisora) pokazała, że samo API przyjmuje identyczny payload bez zająknięcia — wina leżała po stronie ścieżki `set_state`. Ostatecznie publikuję sensor bezpośrednim `POST`-em do REST API, z całym archiwum w atrybucie `months` (atrybuty nie mają limitu 255 znaków, w przeciwieństwie do `input_text` i stanu encji). Szczegóły w Problemach 17–18 powyżej.

Źródłem prawdy jest plik JSON, który przeżywa restarty — sam sensor to tylko warstwa prezentacji, odtwarzana przy każdym starcie skryptu. Dodałem też opcjonalny przycisk „Zarchiwizuj bieżący miesiąc", który robi snapshot niezamkniętego miesiąca od ręki (bez resetu liczników) — przydatny, gdy nie chce się czekać do 1. dnia, żeby zobaczyć dane.

---

## Audyt kodu, czyli co siedziało w skrypcie przez trzy miesiące

Skrypt działał od maja i robił swoje, więc przez długi czas nie było powodu do niego zaglądać. W lipcu usiadłem do porządnego przeglądu całości: linijka po linijce, z pytaniem „czy to na pewno robi to, co myślę". Wyszły cztery błędy, z czego dwa realnie kosztowały mnie energię z magazynu domowego. Żaden nie rzucał się w oczy w logach, bo żaden nie powodował awarii. Po prostu system zachowywał się odrobinę inaczej, niż sądziłem.

**Najciekawszy okazał się błąd w samej regulacji.** Gdy nadeszła chmura i zaczynałem pobierać prąd z sieci, skrypt zamiast zejść z mocy ładowania, *podkręcał* ją. Krok po kroku: 6A, 7A, 8A, aż do maksimum. Powód jest podręcznikowy i dlatego wart opisania: przy imporcie kod gubił informację o tym, jak duży jest deficyt, i podstawiał w to miejsce stałą wartość. A ponieważ do nadwyżki dolicza się moc ładowarki, każda kolejna iteracja widziała „więcej dostępnej mocy" niż poprzednia. Klasyczne dodatnie sprzężenie zwrotne, w pętli, którą sam napisałem i której przez kwartał nie zauważyłem. Ładowanie kończyło się dopiero wtedy, gdy magazyn domowy spadł poniżej 95% i wchodził tryb priorytetu baterii, czyli już po niepotrzebnym cyklu rozładowania.

Przy okazji wyszła rzecz, która zmieniła moje rozumienie własnej instalacji. **Licznik na złączu z siecią nie mówi prawdy o nadwyżce, jeśli ma się falownik hybrydowy.** Sofar w trybie autokonsumpcji aktywnie dopełnia deficyt z magazynu, żeby utrzymać zerowy bilans z siecią. Efekt jest taki, że przy PV 1 kW, domu 5 kW i aucie ciągnącym 4 kW licznik pokazuje spokojne zero, a bateria w garażu po cichu się opróżnia. Teraz nadwyżkę liczę jako minimum z dwóch rzeczy: tego, co faktycznie wypycham do sieci, i tego, co zostaje z produkcji po odjęciu zużycia domu. Pierwsze pilnuje, żeby nie podbierać mocy ładującej się baterii, drugie widzi deficyt, który bateria maskuje.

**Drugi poważny błąd był bardziej perfidny, bo powstał przy naprawianiu innego błędu.** W maju walczyłem z tym, że skrypt co 30 sekund wysyłał do wallboxa komendę STOP i słychać było klikanie stycznika (Problem 13). Naprawa była prosta: zapamiętuj, co ostatnio wysłałeś, i nie powtarzaj. Tyle że zapamiętywanie działo się także wtedy, gdy wysyłka się nie udała. Wystarczył jeden zgubiony pakiet Wi-Fi (a wallbox stoi w garażu, zasięg bywa marny), żeby skrypt do końca życia procesu był przekonany, że komendę wysłał. W praktyce: ładowanie nie ruszało, dopóki czegoś nie zrestartowałem, albo, w drugą stronę, auto ładowało się mimo trybu priorytetu baterii.

Ten drugi wariant potwierdził się w najlepszy możliwy sposób: dokładnie w chwili wgrywania poprawki. Auto ciągnęło wtedy 5,2 kW przy magazynie naładowanym w 46%, czyli w sytuacji, w której skrypt od dawna powinien był je zatrzymać. Nie zatrzymywał, bo miał zapisane, że już to zrobił. Nowa wersja ucięła sesję w pierwszej iteracji po restarcie. Trudno o lepszy dowód, że błąd nie był teoretyczny.

Do tego doszły dwie rzeczy z gatunku „cicha awaria". Biblioteka TinyTuya przy problemach z siecią nie zgłasza wyjątku, tylko zwraca słownik z kluczem `Error`, a stary kod interpretował to jako „ładowarka gotowa do pracy". I plik z licznikami energii oraz dziesięcioletnim archiwum zapisywał się nieatomowo, więc jedno przerwanie w złym momencie mogło go uszkodzić tak, że od tej pory każdy kolejny zapis cicho padał, a liczniki wracały do zera.

### Czego się nauczyłem o testowaniu takich systemów

Napisałem do skryptu zestaw prostych testów, bez żadnego frameworka, podmieniając AppDaemon i TinyTuya atrapami. Ale najwięcej dała nie tabelka testów, tylko **symulacja całego dnia**: słońce, chmura, powrót słońca, wieczór, z wallboxem reagującym na komendy jak prawdziwy. Dopiero ona pokazała błąd, którego testy jednostkowe nie widziały, bo dotyczył kolejności operacji. Uśrednianie odczytów robiłem *przed* doliczeniem poboru ładowarki, przez co średnia mieszała próbki mierzone przy różnej mocy ładowania i tuż po starcie sesji prąd skakał na maksimum.

Wniosek na przyszłość jest chyba taki: przy sterowaniu ze sprzężeniem zwrotnym sprawdzanie pojedynczych funkcji to za mało. Trzeba puścić pętlę w czasie i zobaczyć, dokąd zbiega. Po poprawkach symulacja wygląda tak, jak powinna: 11A stabilnie w pełnym słońcu, przy chmurze redukcja 10, 8, 7, 6 amperów, stop, a potem płynny powrót w górę.

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

Pełny skrypt AppDaemon dostępny na moim GitHubie: [github.com/tomasz-kwietniewski/ha-ev-charger](https://github.com/tomasz-kwietniewski/ha-ev-charger). Dane urządzenia (Device ID, Local Key, IP) trzymam w osobnym pliku `ev_charger_secrets.json` który nie trafia do repozytorium — szablon znajdziesz w repo jako `ev_charger_secrets.json.example`. Poniżej kluczowe fragmenty kodu:

**Odczyt danych z ładowarki z obsługą PAUSE:**

```python
CHARGER_READY_STATES   = {"PAUSE", "SLEEP", "IDLE", "UNKNOWN"}
CHARGER_WORKING_STATES = {"WORKING"}

def _get_charger_data(self):
    raw = self._device.status()
    # tinytuya zwraca błąd jako dict, nie wyjątek (Problem 21)
    if not isinstance(raw, dict) or "Error" in raw:
        raise RuntimeError(f"tinytuya zwrocil blad: {raw!r}")
    dps = raw.get("dps", {})

    status  = str(dps.get("109", "unknown")).upper()
    current = int(dps.get("150", 0))

    metrics = json.loads(dps.get("102", "{}"))
    l1 = metrics.get("L1", [0, 0, 0])
    l2 = metrics.get("L2", [0, 0, 0])
    l3 = metrics.get("L3", [0, 0, 0])
    power_w = (l1[2] + l2[2] + l3[2]) * 100  # skala x100!

    return {"status": status, "current_a": current, "power_w": power_w}
```

**Obliczanie nadwyżki z uśrednianiem:**

```python
# Sofar: dodatni PCC = eksport (nadwyżka), ujemny = import.
# min() bierze wariant konserwatywny — PV-dom widzi deficyt maskowany
# przez rozładowanie magazynu, PCC pilnuje mocy idącej do baterii.
surplus_without_ev_kw = min(grid_power, pv_power - load_power)

# Pobór auta siedzi już w load_power — doliczamy go z powrotem PRZED
# uśrednianiem, żeby każda próbka w historii znaczyła to samo.
available_kw = surplus_without_ev_kw + charger_power_kw

# Uśredniamy ostatnie 3 odczyty (90s) żeby wyeliminować migotanie
self._surplus_history.append(available_kw)
if len(self._surplus_history) > PCC_HISTORY_SIZE:
    self._surplus_history.pop(0)
avg_available_kw = sum(self._surplus_history) / len(self._surplus_history)

# Bias +1000W — agresywniejsze wykorzystanie nadwyżek.
# BEZ podłogi: przy imporcie wychodzi ujemne, więc regulacja redukuje prąd.
surplus_w = avg_available_kw * 1000 + SURPLUS_BIAS_W
```

**Logika decyzyjna z sześcioma trybami:**

```python
def _decide(self, ha_data, charger_data):
    # 1. EMERGENCY — najwyższy priorytet
    if self._is_emergency_active():
        if soc < SOC_EMERGENCY_MIN:
            return ("BATTERY_PRIORITY", 0)
        return ("EMERGENCY", EMERGENCY_CURRENT_A)  # 13A

    # 2. Ujemna cena energii
    if price < 0:
        return ("NEGATIVE_PRICE", NEGATIVE_PRICE_CURRENT_A)  # 13A — bufor na dom

    # 3. Tryb zimowy — nocne ładowanie
    if winter_mode and in_night_window:
        return ("WINTER_NIGHT", WINTER_MAX_CURRENT)  # 10A

    # 4. Ochrona baterii
    if soc < SOC_THRESHOLD:  # 95%
        return ("BATTERY_PRIORITY", 0)

    # 5. Tryb solarny
    if available_surplus >= START_SURPLUS_W:  # 1600W
        current = max(6, min(16, int(available_surplus / (3 * 230))))
        return ("SOLAR", current)

    return ("IDLE", 0)
```

**Tryb EMERGENCY z automatycznym timerem:**

```python
def _on_emergency_toggle(self, entity, attribute, old, new, kwargs):
    if new == "on":
        hours = self._get_emergency_hours()  # z input_number
        self._emergency_end_time = datetime.datetime.now() + datetime.timedelta(hours=hours)
        self._clear_schedule()  # wyczyść harmonogram przed startem
    else:
        self._emergency_end_time = None

def _is_emergency_active(self):
    if self.get_state(EMERGENCY_MODE_ENTITY) != "on":
        return False
    if datetime.datetime.now() > self._emergency_end_time:
        # Czas minął — wyłącz automatycznie
        self.call_service("input_boolean/turn_off", entity_id=EMERGENCY_MODE_ENTITY)
        return False
    return True
```

---

## Podsumowanie

Inteligentne ładowanie auta elektrycznego z nadwyżek PV nie wymaga drogiego sprzętu. Wystarczy:

1. Tania ładowarka z Wi-Fi i protokołem Tuya (~1150 zł)
2. Home Assistant jako centrum automatyki
3. Biblioteka TinyTuya do lokalnej kontroli
4. Trochę Pythona w AppDaemon

System obsługuje sześć trybów pracy: solarny (proporcjonalnie do nadwyżek), awaryjny (ładuj teraz na maksa), ujemne ceny (operator płaci), zimowy (nocna taryfa), priorytet baterii i bezczynność. Wszystko sterowane z poziomu dashboardu HA.

Efekt: auto ładuje się za darmo gdy świeci słońce, a przy ujemnych cenach Pstryk — operator energii dopłaca za to, że pobieramy prąd.

Latem planujemy naładować całą baterię 75 kWh praktycznie bez kosztów. Policzymy to jesienią.

---

*Artykuł napisany na podstawie rzeczywistej instalacji. Pierwsza wersja: maj 2026. Aktualizacja: maj 2026 — dodano tryb EMERGENCY, obsługę stanu PAUSE, uśrednianie PCC, obniżenie progu startu do 1600W. Aktualizacja 2: maj 2026 — uśrednianie PCC rozszerzone do 3 próbek (90s), bias wydzielony jako nazwana stała SURPLUS_BIAS_W, poprawka komentarzy znaku PCC. Aktualizacja 3: 12 maja 2026 — dodano Problem 12 (AppDaemon skanuje apps/ rekurencyjnie — duplikaty aplikacji przy backupie wewnątrz folderu). Aktualizacja 4: 8 czerwca 2026 — Problemy 13–16 (STOP-spam w gałęzi IDLE, zamrożony DP 102 w firmware dé EV v2.9.4, chmura Tuya a harmonogram DP 151, ukryte pole `e` = energia sesji × 0,1 kWh); archiwum historii miesięcznej z retencją 10 lat — wykres i tabela porównawcza na dashboardzie, ręczny przycisk archiwizacji (Problemy 17–18: dane ginące przy resecie miesiąca oraz `set_state` 400 w HA 2026.x -> publikacja przez REST API rdzenia). Aktualizacja 6: 28 lipca 2026 — Problem 23: regulacja goniąca szum (prąd zmieniany co 30 s, sekwencje 10A → 11A → 10A). Histereza ±250 W wokół progu stopnia plus potwierdzenie zmiany przez 2 iteracje; duży spadek nadal natychmiastowy. Zmierzone: 52 → 1 zmiana w pochmurne pół godziny. Aktualizacja 5: 27 lipca 2026 — audyt kodu, Problemy 19–22: regulacja SOLAR „uciekająca" w górę przy zachmurzeniu (nadwyżka liczona teraz jako `min(PCC, PV − dom)` bez podłogi), dedup komend START/STOP bez ponowień, TinyTuya zwracająca błąd jako dict zamiast wyjątku, nieatomowy zapis pliku persistent; tryb NEGATIVE_PRICE zszedł z 16A na 13A (bufor na dom), doszły lekkie testy jednostkowe w `tests/`. Aktualizacja 7: 11 sierpnia 2026 - Problem 24: wallbox zawieszony przez 36 godzin (odpowiadał w sieci, ale nie aktualizował danych i ignorował wszystkie komendy), a system tego nie zauważył. Wykrywanie po niezmiennym surowym DP 102 zamiast po samym zerze mocy, powiadomienie w Home Assistant zamiast WARNING w logu, cykl budzenia sesji przy statusie WORKING bez poboru, weryfikacja zadanego prądu przez DP 150, koniec z trwałym odpuszczaniem prób startu. Testy jednostkowe: 24 -> 50. Aktualizacja 8: 20 sierpnia 2026 - Problem 25: ta sama awaria wróciła i mimo sześciu poprawnych alarmów trwała 22,5 godziny (min. 12,2 kWh nadwyżki do sieci), bo powiadomienie szło wyłącznie do panelu HA. Alarm idzie teraz pushem na telefon, doszedł mechanizm automatycznego restartu wallboxa (reaktywny plus profilaktyka nocna), na razie uśpiony - kod komendy restartu okazał się punktem tylko do zapisu, którego nie da się podsłuchać, a DP 142 z tuya-local nie działa na firmware 2.9.4. Przy okazji: `cp` w DP 106 to napięcie Control Pilot, nie wersja, oraz naprawiony status `IDLEINS`, przez który skrypt widział podłączone auto jako odpięte. Testy jednostkowe: 50 -> 67. Poszukiwania komendy restartu zamkniete: chmura Tuya nie ma modelu tego wallboxa (`functions: []`, kategoria `dj` czyli lampa), wiec ani kanal deweloperski, ani aplikacyjny nie kojarzy z nim zadnej komendy - aplikacja Smart Life steruje nim wlasnym panelem producenta, omijajacym publiczny model.*
