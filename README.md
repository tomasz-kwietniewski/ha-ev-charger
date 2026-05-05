# ha-ev-charger

Inteligentne ładowanie samochodu elektrycznego z nadwyżek fotowoltaiki — Home Assistant + AppDaemon + TinyTuya.

## Co to robi

Skrypt AppDaemon steruje ładowarką EV (protokół Tuya 3.5) lokalnie przez sieć domową. Co 30 sekund sprawdza stan instalacji i podejmuje decyzję:

| Tryb | Warunek | Działanie |
|------|---------|-----------|
| `SOLAR` | SOC baterii ≥ 95% i nadwyżka PV ≥ 5 kW | Ładuj proporcjonalnie do nadwyżki (6–16A) |
| `NEGATIVE_PRICE` | Cena Pstryk < 0 zł/kWh | Ładuj na maksimum (16A) |
| `WINTER_NIGHT` | Tryb zimowy włączony, godz. 22–6 | Ładuj na 10A (tania taryfa nocna) |
| `BATTERY_PRIORITY` | SOC < 95% | Czekaj, priorytet ładowania baterii |
| `IDLE` | Brak nadwyżek lub auto niepodłączone | Ładowarka wyłączona |

## Instalacja

### Wymagania

- Home Assistant (HAOS lub supervised)
- Add-on AppDaemon
- Ładowarka EV z protokołem Tuya 3.5
- Falownik Sofar HYD przez integrację SolaX Inverter Modbus
- Integracja [ha_Pstryk](https://github.com/balgerion/ha_Pstryk_card) (dynamiczne ceny energii)

### Krok 1 — AppDaemon

W konfiguracji AppDaemon dodaj TinyTuya do `python_packages`:

```yaml
appdaemon:
  python_packages:
    - tinytuya
```

### Krok 2 — Skrypt

Skopiuj `appdaemon/apps/ev_charger.py` i `appdaemon/apps.yaml` do:
```
/addon_configs/a0d7b954_appdaemon/apps/
```

Uzupełnij w skrypcie swoje dane urządzenia:
```python
DEVICE_ID  = "TWOJ_DEVICE_ID"
DEVICE_IP  = "192.168.X.X"
DEVICE_KEY = "TWOJ_LOCAL_KEY"
```

### Krok 3 — Helpery w HA

Utwórz przez UI (Settings → Helpers) — **nie przez YAML**:

| Typ | Entity ID |
|-----|-----------|
| Text | `input_text.ev_charger_status` |
| Text | `input_text.ev_charger_mode` |
| Text | `input_text.ev_data` |
| Toggle | `input_boolean.ev_tryb_zimowy` |

### Krok 4 — Template sensory

Dodaj zawartość `homeassistant/configuration.yaml` do swojego `/config/configuration.yaml`.

## Struktura plików

```
ha-ev-charger/
├── CLAUDE.md                          ← kontekst projektu dla Claude Code
├── README.md
├── appdaemon/
│   ├── apps/ev_charger.py             ← główny skrypt sterujący
│   └── apps.yaml                      ← rejestr aplikacji AppDaemon
├── homeassistant/
│   └── configuration.yaml             ← template sensory EV
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
- **DP 151 (harmonogram) blokuje START** — skrypt czyści go przy każdym starcie
- **Znak PCC Sofar** — może się zmienić po zmianie trybu falownika, zawsze weryfikuj empirycznie
- **Helpery tylko przez UI** — encje zdefiniowane w YAML są read-only dla serwisów HA

Szczegóły w `docs/ladowanie_ev_z_nadwyzek_pv.md`.

## Licencja

MIT
