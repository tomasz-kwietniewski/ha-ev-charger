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
```

Nadwyżka dla trybu SOLAR to `min(eksport PCC, PV − zużycie domu) + pobór ładowarki`, uśredniona przez 3 odczyty i powiększona o `SURPLUS_BIAS_W`. Samo PCC nie wystarcza, bo falownik hybrydowy w trybie self-use trzyma PCC blisko zera rozładowując magazyn i deficyt byłby niewidoczny (skrypt podkręcałby prąd kosztem baterii domowej). Przy imporcie nadwyżka jest ujemna (bez podłogi), dzięki czemu regulacja redukuje prąd i histereza STOP faktycznie działa. Pobór ładowarki doliczany jest **przed** uśrednianiem — inaczej średnia miesza próbki mierzone przy różnej mocy ładowania i prąd skacze tuż po starcie sesji (szczegóły: docs, Problem 19).

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
- **DP 102 potrafi się „zamrozić"** — firmware dé EV Charger v2.9.4 czasami przestaje aktualizować cały blok pomiarów (L1/L2/L3 + pola `p`/`e`/`t`); status nadal `WORKING`, ale moc zwracana to 0 W mimo realnego ładowania. Lekarstwo: **Reboot z aplikacji Smart Life** (Settings → Reboot, nie Reset to Factory). Skrypt ma watchdog ostrzegający w logach po 10 min utrzymującego się WORKING+0W w aktywnym trybie ładowania.
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

Pokrywa logikę decyzyjną (`_decide`, `_surplus_to_current`), liczenie nadwyżki przy deficycie i maskowaniu przez magazyn, ponowienia komend START/STOP, detekcję błędów TinyTuya, atomowość persystencji i limit 255 znaków `input_text.ev_data`. Warto uruchomić przed każdym `./deploy.sh`.

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

## Licencja

MIT
