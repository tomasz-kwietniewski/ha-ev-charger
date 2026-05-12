# ha-ev-charger

Inteligentne ładowanie samochodu elektrycznego z nadwyżek fotowoltaiki — Home Assistant + AppDaemon + TinyTuya.

## Co to robi

Skrypt AppDaemon steruje ładowarką EV (protokół Tuya 3.5) lokalnie przez sieć domową. Co 30 sekund sprawdza stan instalacji i podejmuje decyzję:

| Tryb | Warunek | Działanie |
|------|---------|-----------|
| `EMERGENCY` | Włączony ręcznie przez toggle w HA | Ładuj natychmiast na 13A (~9 kW), niezależnie od PV i cen |
| `NEGATIVE_PRICE` | Cena Pstryk < 0 zł/kWh | Ładuj na maksimum (16A) |
| `WINTER_NIGHT` | Tryb zimowy włączony, godz. 22–6 | Ładuj na 10A (tania taryfa nocna) |
| `SOLAR` | SOC baterii ≥ 95% i nadwyżka PV ≥ 1,6 kW | Ładuj proporcjonalnie do nadwyżki (6–16A) |
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

Skopiuj `ev_charger_secrets.json.example` jako `ev_charger_secrets.json` do katalogu add-onu AppDaemon i uzupełnij danymi urządzenia:

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

### Krok 5 — Template sensory i utility meters

Dodaj zawartość `homeassistant/configuration.yaml` do swojego `/config/configuration.yaml` i zrestartuj HA.

Tworzone są m.in.:
- `sensor.ev_status_opis` — status ładowarki po polsku
- `sensor.ev_tryb_opis` — aktywny tryb po polsku
- `sensor.samowystarczalnosc_dzis` — samowystarczalność energetyczna dziś [%]
- `sensor.samowystarczalnosc_miesiac` — samowystarczalność energetyczna miesiąc [%]
- utility meters miesięczne: zużycie domu, produkcja PV, import, eksport

### Krok 6 — Dashboard

Dodaj kartę z `homeassistant/lovelace_ev_card.yaml` do swojego dashboardu. Zawiera panel sterowania trybem awaryjnym, status ładowania i statystyki energii.

## Konfiguracja — ważne stałe

```python
SOC_THRESHOLD     = 95   # [%] poniżej - nie ładuj auta (ochrona baterii)
SOC_EMERGENCY_MIN = 20   # [%] w trybie EMERGENCY zatrzymaj gdy SOC < tej wartości
MIN_CURRENT_A     = 6    # [A] minimum ładowarki
MAX_CURRENT_A     = 16   # [A] maksimum ładowarki
EMERGENCY_CURRENT_A = 13 # [A] tryb emergency (~9 kW, bufor 2 kW na dom)
START_SURPLUS_W   = 1600 # [W] min nadwyżka do startu (razem z SURPLUS_BIAS_W)
STOP_SURPLUS_W    = 1200 # [W] poniżej - zatrzymaj ładowanie (histereza)
SURPLUS_BIAS_W    = 1000 # [W] bufor doliczany do PCC — start już przy ~0,6 kW eksportu
PCC_HISTORY_SIZE  = 3    # ile odczytów uśredniać (3 * 30s = 90s)
```

## Struktura plików

```
ha-ev-charger/
├── CLAUDE.md                          ← kontekst projektu dla Claude Code
├── README.md
├── appdaemon/
│   ├── apps/ev_charger.py             ← główny skrypt sterujący
│   └── apps.yaml                      ← rejestr aplikacji AppDaemon
├── homeassistant/
│   ├── configuration.yaml             ← template sensory + utility meters
│   └── lovelace_ev_card.yaml          ← karta dashboardu z trybem awaryjnym
├── ev_charger_secrets.json.example    ← szablon danych urządzenia
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
- **Moc DP 102 × 100** — wartości mocy per faza są mnożone przez 100, `32` oznacza 3200W
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
