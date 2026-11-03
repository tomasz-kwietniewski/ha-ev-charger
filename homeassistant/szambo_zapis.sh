#!/bin/sh
# Dopisuje jeden wiersz historii szamba do /config/szambo_historia.csv.
# Wołane przez shell_command.szambo_zapis z automatyzacji szambo_historia;
# wiersz składa HA, tu tylko nagłówek przy pierwszym zapisie i dopisanie.
# Plik trzymamy na zawsze - recorder kasuje stany po 365 dniach.
F=/config/szambo_historia.csv
[ -f "$F" ] || echo "zapisano;zdarzenie;czas_zdarzenia;pct;cm_do_pelna;poprzednie_oproznienie;dni_od_oproznienia" > "$F"
echo "$1" >> "$F"
