#!/bin/sh
# Dopisuje jeden wiersz historii szamba do /config/szambo_historia.csv.
# Wołane przez shell_command.szambo_zapis z automatyzacji szambo_historia;
# wiersz składa HA, tu tylko nagłówek przy pierwszym zapisie i dopisanie.
# Plik trzymamy na zawsze - recorder kasuje stany po czasie z purge_keep_days.
# BOM na początku, żeby Excel otworzył UTF-8 z polskimi znakami.
F=/config/szambo_historia.csv
if [ ! -f "$F" ]; then
  printf '\357\273\277' > "$F"
  echo "zapisano;zdarzenie;czas_zdarzenia;pct;cm_do_pelna;poprzednie_oproznienie;dni_od_oproznienia;koszt_zl;m3;firma;zrodlo;uwagi" >> "$F"
fi
echo "$1" >> "$F"
