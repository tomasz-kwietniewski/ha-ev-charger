# ha-ev-charger

Inteligentne ładowanie samochodu elektrycznego z nadwyżek fotowoltaiki — Home Assistant + AppDaemon + TinyTuya.

## Co to robi

Skrypt AppDaemon steruje ładowarką EV (protokół Tuya 3.5) lokalnie przez sieć domową. Co 30 sekund sprawdza stan instalacji i podejmuje decyzję:

| Tryb | Warunek | Działanie |
|------|---------|-----------|
| `EMERGENCY` | Włączony ręcznie przez toggle w HA | Ładuj natychmiast na 13A (~9 kW), niezależnie od PV i cen |
| `NEGATIVE_PRICE` | Cena Pstryk < 0 zł/kWh | Ładuj na 13A (~9 kW, bufor ~2 kW na dom przy przyłączu 11 kW) |
| `WINTER_NIGHT` | Tryb zimowy włączony, godz. 22–6 | Ładuj na 10A (tania taryfa nocna) |
| `SOLAR` | SOC baterii ≥ 95% i nadwyżka ≥ 1,6 kW | Ładuj proporcjonalnie do nadwyżki (6–16A); nadwyżka = min(eksport PCC, PV − zużycie domu) + bias |
| `BATTERY_PRIORITY` | SOC < 95% | Czekaj, priorytet ładowania baterii |
| `IDLE` | Brak nadwyżek lub auto niepodłączone | Ładowarka wyłączona |

Tryby sprawdzane są w kolejności od góry — EMERGENCY ma najwyższy priorytet.

## Instalacja

### Wymagania

- Home Assistant (HAOS lub supervised)
- Add-on AppDaemon
- Ładowarka EV z protokołem Tuya 3.5
- Falownik Sofar HYD przez integrację SolaX Inverter Modbus
- Integracja [ha_Pstryk](https://github.com/balgerion/ha_Pstryk_card) (dynamiczne ceny energii, opcjonalna)

### Krok 1 — AppDaemon

W konfiguracji AppDaemon dodaj TinyTuya do `python_packages`:

```yaml
appdaemon:
  python_packages:
    - tinytuya
```

### Krok 2 — Secrets

Skopiuj `appdaemon/ev_charger_secrets.json.example` jako `ev_charger_secrets.json` do katalogu add-onu AppDaemon i uzupełnij danymi urządzenia:

```
/addon_configs/a0d7b954_appdaemon/ev_charger_secrets.json
```

> **Ważne:** AppDaemon mapuje `/config/` na swój własny katalog add-onu (`/addon_configs/a0d7b954_appdaemon/`), **nie** na główny `/config/` HA. Plik sekretów musi leżeć w katalogu add-onu, a nie w `/config/`.

```json
{
  "device_id": "TWOJ_DEVICE_ID",
  "device_ip": "192.168.X.X",
  "device_key": "TWOJ_LOCAL_KEY"
}
```

Jak pobrać Local Key — [instrukcja w dokumentacji TinyTuya](https://github.com/jasonacox/tinytuya#setup-wizard---getting-local-keys).

> **Jeśli masz plik w obu lokalizacjach** (`/config/ev_charger_secrets.json` i `/addon_configs/.../ev_charger_secrets.json`) — ten w `/config/` jest martwym artefaktem i można go usunąć. AppDaemon go nie widzi.

### Krok 3 — Skrypt

Skopiuj `appdaemon/apps/ev_charger.py` i `appdaemon/apps.yaml` do:
```
/addon_configs/a0d7b954_appdaemon/apps/
```

### Krok 4 — Helpery w HA

Utwórz przez UI (Settings → Helpers) — **nie przez YAML**:

| Typ | Entity ID | Opis |
|-----|-----------|------|
| Text | `input_text.ev_charger_status` | Status ładowarki |
| Text | `input_text.ev_charger_mode` | Aktywny tryb |
| Text | `input_text.ev_data` | JSON z danymi sesji |
| Toggle | `input_boolean.ev_tryb_zimowy` | Tryb zimowy (nocne ładowanie 22–6) |
| Toggle | `input_boolean.ev_tryb_awaryjny` | Tryb awaryjny (ładuj na maksa teraz) |
| Number | `input_number.ev_awaryjny_godziny` | Czas trybu awaryjnego (min: 0,5 / max: 8 / step: 0,5 / unit: h) |
| Button | `input_button.ev_archiwizuj_teraz` | Ręczna archiwizacja bieżącego miesiąca (opcjonalny, do testów/podglądu) |
| Toggle | `input_boolean.ev_auto_restart` | Automatyczny restart zawieszonego wallboxa (opcjonalny — brak encji oznacza „włączone") |

### Krok 5 — Template sensory i utility meters

Dodaj zawartość `homeassistant/configuration.yaml` do swojego `/config/configuration.yaml` i zrestartuj HA.

Tworzone są m.in.:
- `sensor.ev_status_opis` — status ładowarki po polsku
- `sensor.ev_tryb_opis` — aktywny tryb po polsku
- `sensor.samowystarczalnosc_dzis` — samowystarczalność energetyczna dziś [%]
- `sensor.samowystarczalnosc_miesiac` — samowystarczalność energetyczna miesiąc [%]
- utility meters miesięczne: zużycie domu, produkcja PV, import, eksport

> Sensor `sensor.ev_historia_miesieczna` (archiwum miesiąc do miesiąca) jest publikowany przez AppDaemon, nie definiujesz go w YAML. Patrz sekcja [Historia miesięczna](#historia-miesiczna).

### Krok 6 — Dashboard

Dla archiwum miesiąc do miesiąca dodaj karty z `homeassistant/lovelace_ev_history_card.yaml` (wykres słupkowy + tabela + przycisk ręcznej archiwizacji). Wykres słupkowy wymaga karty `apexcharts-card` z HACS; tabela Markdown działa natywnie, bez HACS.

Panel sterowania (toggle trybu awaryjnego/zimowego, status, statystyki) złóż z sensorów opisowych (`sensor.ev_status_opis`, `sensor.ev_tryb_opis`, `sensor.ev_moc_ladowania` itd.) w dowolnej karcie Entities — repo nie narzuca gotowego layoutu.

## Historia miesięczna

Utility meters i wewnętrzny licznik energii ładowarki zerują się 1. dnia miesiąca — bez archiwizacji dane poprzedniego miesiąca przepadały (trafiały tylko do logu). Skrypt zapisuje teraz zamknięty miesiąc do trwałego archiwum, dzięki czemu można cofać się w czasie i porównywać miesiąc do miesiąca.

**Jak to działa:**

- W każdej iteracji (co 30 s) skrypt zapamiętuje bieżące wartości liczników miesięcznych (`_um_snapshot`).
- Przy przełomie miesiąca — **zanim** wyzeruje licznik — archiwizuje snapshot z poprzedniej iteracji (stan na koniec starego miesiąca). Dzięki temu wynik nie zależy od tego, czy utility_meter zdążył się już zresetować.
- Po restarcie AppDaemona (pusty snapshot) używany jest fallback: atrybut `last_period` liczników utility_meter.
- Znacznik miesiąca (`ev_last_ym`) jest trwały, więc archiwizacja zadziała nawet, gdy serwer wstanie dopiero po 1. dniu miesiąca.

**Gdzie trzymane są dane:** plik `ev_charger_data.json` w katalogu add-onu AppDaemon, klucz `ev_history` (lista do 120 miesięcy ≈ 10 lat). To samo źródło, co liczniki energii — przeżywa restarty HA.

**Sensor:** AppDaemon publikuje `sensor.ev_historia_miesieczna` bezpośrednim POST-em do REST API rdzenia HA (AppDaemon `set_state` na `sensor.*` zwraca 400 w HA 2026.x — patrz docs, Problem 18). Stan = energia EV ostatniego zarchiwizowanego miesiąca, a atrybut `months` zawiera całe archiwum:

```json
{
  "ym": "2026-05",
  "ev_kwh": 142.6,
  "produkcja_kwh": 890.3,
  "zuzycie_kwh": 612.1,
  "import_kwh": 78.4,
  "eksport_kwh": 410.2,
  "samowystarczalnosc": 87.2
}
```

Wizualizacja (wykres + tabela) — patrz `homeassistant/lovelace_ev_history_card.yaml`.

**Ręczna archiwizacja (opcjonalna):** pierwszy wpis pojawia się dopiero przy najbliższym przełomie miesiąca. Aby zobaczyć/przetestować archiwum od razu, utwórz pomocnik `input_button.ev_archiwizuj_teraz` (przez UI) i dodaj kartę przycisku z `lovelace_ev_history_card.yaml`. Naciśnięcie archiwizuje bieżący (niezamknięty) miesiąc z aktualnymi danymi, bez resetu liczników — wpis jest idempotentny po `YYYY-MM`, więc przy realnym przełomie zostanie nadpisany wartością końcową.

## Konfiguracja — ważne stałe

```python
SOC_THRESHOLD     = 95   # [%] poniżej - nie ładuj auta (ochrona baterii)
SOC_EMERGENCY_MIN = 20   # [%] w trybie EMERGENCY zatrzymaj gdy SOC < tej wartości
MIN_CURRENT_A     = 6    # [A] minimum ładowarki
MAX_CURRENT_A     = 16   # [A] maksimum ładowarki
EMERGENCY_CURRENT_A      = 13  # [A] tryb emergency (~9 kW, bufor 2 kW na dom)
NEGATIVE_PRICE_CURRENT_A = 13  # [A] ujemna cena — też z buforem na dom
START_SURPLUS_W   = 1600 # [W] min nadwyżka do startu (razem z SURPLUS_BIAS_W)
STOP_SURPLUS_W    = 1200 # [W] poniżej - zatrzymaj ładowanie (histereza)
SURPLUS_BIAS_W    = 1000 # [W] bufor doliczany do nadwyżki — start już przy ~0,6 kW eksportu
PCC_HISTORY_SIZE  = 3    # ile odczytów uśredniać (3 * 30s = 90s)
WATCHDOG_FROZEN_DP_THRESHOLD = 20  # iteracji WORKING+0W zanim watchdog ostrzeże (=10 min)
SWITCH_RETRY_ITERATIONS  = 4  # co ile iteracji ponawiać START/STOP przy niezgodności stanu
SWITCH_MAX_START_RETRIES = 3  # ile razy ponawiać START zanim skrypt odpuści (auto pełne?)
CURRENT_STEP_MARGIN_W = 250   # [W] histereza wokół progu stopnia prądu (w obie strony)
CURRENT_HOLD_ITERS    = 2     # ile iteracji nowy cel musi się utrzymać przed wysłaniem
CURRENT_FAST_DROP_A   = 3     # [A] spadek o tyle lub więcej idzie natychmiast (ochrona przyłącza)
CURRENT_VERIFY_ITERS  = 2     # ile iteracji tolerancji, zanim uznamy że prąd się nie przyjął
FROZEN_METRICS_THRESHOLD   = 10  # identycznych odczytów DP 102 = zawieszony wallbox (=5 min)
HEALTH_ACTIVE_GRACE_ITERS  = 6   # okno tolerancji migotania trybu przy wykrywaniu awarii
UNRESPONSIVE_CMD_THRESHOLD = 4   # ile komend bez efektu zanim powiadomimy o awarii
START_RETRY_COOLDOWN_ITERS = 60  # pauza po serii nieudanych STARTów (=30 min), potem próba znowu
WAKE_CYCLE_AFTER_ITERS     = 30  # WORKING bez poboru przez 15 min -> cykl budzenia sesji
WAKE_CYCLE_MAX_ATTEMPTS    = 2   # twardy limit budzeń (każdy STOP to cykl stycznika)
REBOOT_DP             = None # DP komendy restartu — None = automat śpi (patrz niżej)
REBOOT_MAX_ATTEMPTS   = 3    # ile restartów w jednej awarii, zanim zawołamy człowieka
REBOOT_COOLDOWN_ITERS = 20   # odstęp między próbami (=10 min, dłużej niż okno detekcji)
REBOOT_NIGHTLY_HOUR   = 4    # profilaktyczny restart nocny; None = wyłączony
NOTIFY_SERVICE = "notify/notify"  # push na telefon (obok powiadomienia w panelu HA)
CP_CONNECTED_MAX_V = 10.0    # [V] napiecie Control Pilot ponizej progu = auto podlaczone
```

Nadwyżka dla trybu SOLAR to `min(eksport PCC, PV − zużycie domu) + pobór ładowarki`, uśredniona przez 3 odczyty i powiększona o `SURPLUS_BIAS_W`. Samo PCC nie wystarcza, bo falownik hybrydowy w trybie self-use trzyma PCC blisko zera rozładowując magazyn i deficyt byłby niewidoczny (skrypt podkręcałby prąd kosztem baterii domowej). Przy imporcie nadwyżka jest ujemna (bez podłogi), dzięki czemu regulacja redukuje prąd i histereza STOP faktycznie działa. Pobór ładowarki doliczany jest **przed** uśrednianiem — inaczej średnia miesza próbki mierzone przy różnej mocy ładowania i prąd skacze tuż po starcie sesji (szczegóły: docs, Problem 19).

Sam prąd jest dodatkowo **wygładzany**, żeby ładowarka nie zmieniała nastawy co 30 sekund: histereza ±250 W wokół progu stopnia plus wymóg, by nowy cel utrzymał się przez 2 iteracje. Spadek o 3 A lub więcej idzie natychmiast, bo chroni przyłącze 11 kW. Bez tego wystarczało wahanie ±30 W przy nadwyżce stojącej na granicy stopnia, żeby prąd skakał w każdej iteracji (szczegóły: docs, Problem 23).

## Struktura plików

```
ha-ev-charger/
├── README.md
├── deploy.sh                          ← deploy przez SSH (backup + restart + rollback)
├── appdaemon/
│   ├── apps/ev_charger.py             ← główny skrypt sterujący
│   ├── apps.yaml                      ← rejestr aplikacji AppDaemon
│   └── ev_charger_secrets.json.example ← szablon danych urządzenia
├── homeassistant/
│   ├── configuration.yaml             ← template sensory + utility meters
│   └── lovelace_ev_history_card.yaml  ← karty archiwum (wykres + tabela + przycisk)
├── tests/
│   └── test_ev_charger.py             ← lekkie testy jednostkowe (bez zależności)
└── docs/
    └── ladowanie_ev_z_nadwyzek_pv.md  ← artykuł techniczny
```

## Sprzęt

- **Ładowarka:** dé EV Charger 11 kW WiFi Typ 2 (~1150 zł)
- **Falownik:** Sofar HYD 8KTL-3PH
- **Magazyn:** Sofar BTS E15-DS5 (15 kWh)
- **Auto:** Citroën Spacetourer Electric 75 kWh
- **HA:** Synology NAS DS420+

## Kluczowe pułapki techniczne

- **Protokół Tuya 3.5** — Local Tuya nie obsługuje, jedyna droga to TinyTuya przez AppDaemon
- **Klucze DP jako stringi** — `dps.get("109")`, nie `dps.get(109)`
- **DP 151 (harmonogram) blokuje START** — skrypt czyści go przy każdym starcie i przed każdym START
- **Stan PAUSE** — gdy auto podłączone ale harmonogram wstrzymał ładowanie, ładowarka raportuje PAUSE zamiast IDLE; skrypt obsługuje oba stany jako "gotowy do ładowania"
- **Znak PCC Sofara** — w tej instalacji dodatni = eksport, ujemny = import; może być odwrotnie — weryfikuj empirycznie po każdej zmianie trybu falownika
- **Migotanie PCC** — wartość PCC oscyluje ±0,2 kW nawet przy stabilnej pracy; bez uśredniania skrypt niepotrzebnie zmienia prąd co 30 sekund
- **Samo PCC nie wystarcza do regulacji** — falownik hybrydowy w trybie self-use kompensuje deficyt z magazynu, trzymając PCC blisko zera; nadwyżkę liczymy jako `min(PCC, PV − dom)`, inaczej regulacja podkręca prąd kosztem baterii domowej
- **Dedup komend wymaga ponowień** — zapamiętywanie ostatnio wysłanego START/STOP chroni przed spamem, ale bez retry jedna zgubiona komenda blokuje sterowanie na stałe (patrz Problem 19)
- **Moc DP 102 × 100** — wartości mocy per faza są mnożone przez 100, `32` oznacza 3200W
- **DP 102 potrafi się „zamrozić"** — firmware dé EV Charger v2.9.4 czasami przestaje aktualizować cały blok pomiarów (L1/L2/L3 + pola `p`/`e`/`t`); status nadal `WORKING`, ale moc zwracana to 0 W mimo realnego ładowania. Lekarstwo: **Reboot z aplikacji Smart Life** (Settings → Reboot, nie Reset to Factory).
- **Ten sam firmware potrafi zawiesić się głębiej** — urządzenie odpowiada w sieci (ping OK, TinyTuya czyta bez błędu), ale zamrożone są też status DP 109 i wykonywanie komend DP 140: ani START, ani STOP nie robią nic. Trwało to 36 godzin (2026-08-10/11). Sygnatura rozstrzygająca: **surowy DP 102 identyczny co do bitu w kolejnych odczytach** — realne napięcie sieci i temperatura zawsze drgają, a trzy fazy nigdy nie mają tej samej wartości. Skrypt wykrywa to po 5 minutach, tworzy powiadomienie w HA **i wysyła push na telefon** (patrz Problem 24)
- **Samo wykrycie nie wystarcza** — awaria 18-19.08.2026 trwała 22,5 godziny mimo sześciu poprawnych alarmów, bo powiadomienie w panelu HA nikt nie oglądał; zmarnowane minimum 12,2 kWh nadwyżki. Dlatego alarm idzie teraz też pushem, a skrypt ma mechanizm automatycznego restartu: reaktywnie na wykrytą sygnaturę (limit `REBOOT_MAX_ATTEMPTS`, odstęp `REBOOT_COOLDOWN_ITERS`) oraz profilaktycznie raz na dobę o `REBOOT_NIGHTLY_HOUR`, z blokadą, gdy realnie płynie prąd
- **Komenda restartu po LAN nie jest jeszcze ustalona** (`REBOOT_DP = None`) — dopóki tak jest, mechanizm restartu śpi i zachowanie skryptu jest identyczne jak przed jego dodaniem. Protokół lokalny TinyTuya nie zna komendy „reboot", więc musi to być punkt danych **tylko do zapisu** (takim jest u nas np. DP 140 — działa, choć nie widać go w żadnym odczycie). Zbadane na żywym urządzeniu: **DP 142** to wg modelu producenta `x_do_reboot` i **zadziałał dokładnie raz** (2026-08-20 09:17:34, sekundę po zboczu `False → True`, spadek `cp` do 0,0 V) — ale pięć innych prób nie dało nic, więc 1 na 6 to za mało, by oprzeć na tym automat. **DP 188** działa, ale tylko odświeża dane. **DP 141** (`x_do_reset`) pozostaje niesprawdzony — jeśli okaże się resetem do ustawień fabrycznych, wallbox traci sparowanie i local key, więc nie ruszać bez działającego dostępu do Tuya IoT API, którym ten klucz się odzyskuje
- **Dlaczego komenda restartu nie przechodzi przez chmurę** — bo **chmura nie ma modelu tego urządzenia**. Odpytana kanałem aplikacyjnym (`xtend_tuya.call_api`, `source: tuya_sharing`) zwraca dla niego `{"category": "", "functions": [], "status": []}`, a status podaje kategorię `dj` (czyli „lampa") i pustą listę relacji DP. Stąd: Xtend Tuya nie tworzy encji sterujących, komendy po nazwie (`x_do_reboot`) ani po numerze (`"142"`) nie skutkują, a chmura odpowiada `success`, bo przyjmuje polecenie, którego nie ma komu przekazać. Aplikacja Smart Life steruje tym wallboxem własnym panelem producenta, omijającym publiczny model — **pula testów wyczerpana**
- **`xtend_tuya.call_api` nie zwraca wyniku do HA** mimo nazwy „and return the result" (`return_response=True` → błąd walidacji). Odpowiedzi API widać dopiero po `logger.set_level` → `custom_components.xtend_tuya: debug` i w `ha core logs`
- **Komendę restartu wysyłać zboczem, nie wartością** — punkty tylko do zapisu nie raportują swojego stanu (chmura pokazuje `x_do_charge` sprzed miesięcy, choć skrypt wysyła na niego START/STOP codziennie), więc nigdy nie wiadomo, czy punkt nie siedzi już w wartości docelowej. Stąd `False` → pauza → `True`
- **Przy każdym pomiarze na wallboxie robić próbę kontrolną** — identyczny pomiar bez wysyłania komendy. Pierwszy „sukces" DP 142 okazał się zwykłym szumem sieciowym: AppDaemon odpytuje to samo urządzenie równolegle, więc przerwy w łączności i wpychanie harmonogramu przez chmurę zdarzają się same z siebie
- **Ręczny Reboot ze Smart Life nie restartuje całego urządzenia** — moduł WiFi/Tuya pracuje nieprzerwanie (nasłuch co 0,7 s nie zanotował ani jednej przerwy), resetowany jest wyłącznie moduł mocy. Sygnatura: `cp` w DP 106 spada z ~11,7 V do 0,0 V i wraca po ~3 sekundach. To pole to **napięcie Control Pilot**, nie wersja firmware: ~12 V = brak auta, ~9 V = podłączone, ~6 V = ładowanie
- **`IDLE` i `SLEEP` znaczą „nie widzę auta", a nie „gotowy do ładowania"** — i to jest najdroższa pomyłka w tym projekcie. 25.08.2026 auto stało odpięte od 10:25 do 15:34, a skrypt wysłał w tym czasie **72 komendy START do pustego gniazda** i rzucił **dwa fałszywe alarmy o awarii** (pierwszy wisiał pięć godzin). Watchdog zadziałał zgodnie z literą kodu: skrypt „chciał ładować", pomiar stał w miejscu (bo prąd nie płynął), więc uznał zawieszenie. **Fałszywy alarm jest groźniejszy niż zmarnowana energia** — powiadomienie mylące się przy każdym odpiętym aucie w słoneczny dzień przestaje być czytane, i wtedy przepada to prawdziwe
- **Rozstrzyga napięcie Control Pilot, nie status** — wallbox cały czas mówi, czy kabel siedzi w aucie (pole `cp` w DP 106). Próg `CP_CONNECTED_MAX_V` = 10 V leży w połowie między stanem A (~11,7 V, brak auta) i B (~8,6 V, podłączone). Gdy `cp` nie da się odczytać, skrypt zachowuje się jak dawniej — brak danych nie może zablokować ładowania
- **Tryb awaryjny nie przeżywa restartu AppDaemona** — `_emergency_end_time` żyje tylko w pamięci, więc po restarcie (także po każdym `deploy.sh`) `_is_emergency_active()` gasi `input_boolean` i tryb przepada. Przed wdrożeniem w trakcie trwania trybu awaryjnego trzeba poczekać albo liczyć się z przerwaniem ładowania
- **Status `IDLEINS`** — stan przejściowy przy starcie sesji (`PAUSE → IDLE → IDLEINS → WORKING`, trwa ok. 9 s, znaczy „kabel włożony"). Nie był na żadnej liście stanów, więc `_decide()` widział go jako „auto niepodłączone", a zawieszenie w tym stanie byłoby dla watchdoga niewidoczne. Naprawione 20.08.2026
- **Status `WORKING` to deklaracja, nie fakt** — znaczy tylko tyle, że wallbox ma otwartą sesję, nie że auto pobiera prąd. Nie używać go jako jedynego dowodu, że ładowanie trwa; konfrontować ze świeżością danych, sprzężeniem zwrotnym z komend (DP 150) i realnym przepływem mocy
- **Auto po STOP-ie zamyka sesję i samo jej nie wznawia** — Stellantis (Citroën Spacetourer) wyświetla wtedy „ładowanie zakończone" i czeka na nową negocjację; przy wallboxie tkwiącym w `WORKING` trzeba wymusić cykl STOP → START, żeby przerwać sygnał na Control Pilot
- **DP 107 = `[6, 8, 10, 13, 16]`** — lista poziomów prądu; nierozstrzygnięte, czy to realne ograniczenie API, czy tylko presety w Smart Life. Skrypt zadaje dowolne 6-16 A, a `_verify_current` zaloguje WARNING, jeśli wallbox nie przyjmie wartości spoza tej listy
- **DP 102 ma ukryte pole `e` = energia sesji × 0,1 kWh** — niezależny od naszego liczenia `power × dt`, można użyć jako kontrolny licznik energii sesji
- **DP 105 = historia ostatniej sesji** — JSON z `t` (timestamp), `s/e` (start/end), `d` (duration), `c` (kWh × 10); aktualizowany przez wallbox po zakończeniu sesji
- **DP 151 a chmura Tuya** — wallbox po reboocie potrafi pobrać z chmury Tuya niezerowy harmonogram (`m:0` znaczy nieaktywny); skrypt czyści przy starcie sesji, dla bezpieczeństwa też przy każdym `initialize()` AppDaemona
- **Helpery tylko przez UI** — encje zdefiniowane w YAML są read-only dla serwisów HA
- **Serwery Tuya dla Polski** — region "Central Europe", serwer Frankfurt AWS (nie Chiny)

Szczegóły w `docs/ladowanie_ev_z_nadwyzek_pv.md`.

## Deploy

Po każdej zmianie kodu użyj skryptu deploy (wymaga Git Bash lub WSL, SSH alias `ha` w `~/.ssh/config`):

```bash
./deploy.sh            # deploy z potwierdzeniem
./deploy.sh --force    # bez pytania (np. w skryptach)
./deploy.sh --dry-run  # podgląd planu bez zmian
```

Skrypt automatycznie: sprawdza składnię Python, tworzy backup z timestampem w `/addon_configs/a0d7b954_appdaemon/_backups/`, wgrywa pliki przez `scp`, restartuje AppDaemon i weryfikuje logi. W razie błędu oferuje rollback.

> **Uwaga:** backupy muszą leżeć **poza** folderem `apps/` — AppDaemon skanuje go rekurencyjnie i załadowałby stare pliki YAML z backupu jako dodatkowe aplikacje.

## Testy

Lekki runner bez zewnętrznych zależności (stubuje AppDaemon, TinyTuya i `requests`):

```bash
python tests/test_ev_charger.py
```

Pokrywa logikę decyzyjną (`_decide`, `_surplus_to_current`), liczenie nadwyżki przy deficycie i maskowaniu przez magazyn, ponowienia komend START/STOP, detekcję błędów TinyTuya, atomowość persystencji, limit 255 znaków `input_text.ev_data`, a także wykrywanie zawieszonego wallboxa, cykl budzenia sesji i weryfikację zadanego prądu. `./deploy.sh` uruchamia je automatycznie i przerywa wdrożenie, gdy któryś nie przejdzie.

Każdy test regresyjny nosi w komentarzu datę i opis zdarzenia, które go wymusiło — dzięki temu widać, przed czym konkretnie chroni dana asercja.

## Konfiguracja środowiskowa

Plik `appdaemon.yaml` nie jest w repo (konfiguracja środowiskowa). Po instalacji ustaw lokalizację i strefę czasową na wartości ze swojego HA (Settings → System → General):

```yaml
appdaemon:
  latitude: 52.1234       # Twoja szerokość geograficzna
  longitude: 20.5678      # Twoja długość geograficzna
  elevation: 95           # Wysokość n.p.m. [m]
  time_zone: Europe/Warsaw
```

Domyślna konfiguracja AppDaemon może mieć ustawione Amsterdam (`latitude: 52.38`, `longitude: 4.90`, `time_zone: Europe/Amsterdam`) — to błędne wartości dla Polski, które mogą wpłynąć na obliczenia astronomiczne (wschód/zachód słońca) jeśli je kiedyś używasz.

## Debugowanie

Logi AppDaemon (terminal HA lub SSH):

```bash
ha apps logs a0d7b954_appdaemon
```

> **Uwaga:** AppDaemon loguje przez supervisor HA, **nie** do pliku `.log` na dysku. Komenda powyżej to jedyna pewna droga do logów.

Domyślnie zwracane jest tylko ostatnie ~100 linii — do analizy historii trzeba podać `-n`:

```bash
ha apps logs a0d7b954_appdaemon -n 3000
```

Gdy ładowanie nie rusza mimo nadwyżki, najszybszy test to sprawdzenie, czy pomiary wallboxa **w ogóle się zmieniają**. Jeśli wszystkie odczyty są identyczne co do bitu, urządzenie jest zawieszone i żadna zmiana w skrypcie nie pomoże — potrzebny jest Reboot ze Smart Life:

```bash
ha apps logs a0d7b954_appdaemon -n 20000 | grep -oE "DP102_raw=.*" | sort | uniq -c
```

## Licencja

MIT
