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

*Artykuł napisany na podstawie rzeczywistej instalacji. Pierwsza wersja: maj 2026. Aktualizacja: maj 2026 — dodano tryb EMERGENCY, obsługę stanu PAUSE, uśrednianie PCC, obniżenie progu startu do 1600W. Aktualizacja 2: maj 2026 — uśrednianie PCC rozszerzone do 3 próbek (90s), bias wydzielony jako nazwana stała SURPLUS_BIAS_W, poprawka komentarzy znaku PCC. Aktualizacja 3: 12 maja 2026 — dodano Problem 12 (AppDaemon skanuje apps/ rekurencyjnie — duplikaty aplikacji przy backupie wewnątrz folderu). Aktualizacja 4: 8 czerwca 2026 — Problemy 13–16 (STOP-spam w gałęzi IDLE, zamrożony DP 102 w firmware dé EV v2.9.4, chmura Tuya a harmonogram DP 151, ukryte pole `e` = energia sesji × 0,1 kWh); archiwum historii miesięcznej z retencją 10 lat — wykres i tabela porównawcza na dashboardzie, ręczny przycisk archiwizacji (Problemy 17–18: dane ginące przy resecie miesiąca oraz `set_state` 400 w HA 2026.x -> publikacja przez REST API rdzenia). Aktualizacja 5: 27 lipca 2026 — audyt kodu, Problemy 19–22: regulacja SOLAR „uciekająca" w górę przy zachmurzeniu (nadwyżka liczona teraz jako `min(PCC, PV − dom)` bez podłogi), dedup komend START/STOP bez ponowień, TinyTuya zwracająca błąd jako dict zamiast wyjątku, nieatomowy zapis pliku persistent; tryb NEGATIVE_PRICE zszedł z 16A na 13A (bufor na dom), doszły lekkie testy jednostkowe w `tests/`.*
