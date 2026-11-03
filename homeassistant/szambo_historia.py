"""Podsumowanie historii szamba dla HA (command_line sensor.szambo_historia).

Czyta /config/szambo_historia.csv (szambo_zapis.sh plus wiersze dopisane ręcznie
z wyciągu i skanów potwierdzeń) i wypisuje JSON: state = liczba wywozów,
atrybuty = wywozy od najnowszego, podsumowanie roczne, wydatki jednorazowe.
Działa w kontenerze HA - tylko biblioteka standardowa.
"""
import csv
import json
from datetime import datetime

PLIK = "/config/szambo_historia.csv"


def czas(tekst):
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime((tekst or "").strip(), fmt)
        except ValueError:
            pass
    return None


def liczba(tekst):
    try:
        return float((tekst or "").replace(",", ".").strip())
    except ValueError:
        return None


def main():
    try:
        with open(PLIK, encoding="utf-8-sig", newline="") as f:
            wiersze = list(csv.DictReader(f, delimiter=";"))
    except FileNotFoundError:
        wiersze = []

    wywozy, jednorazowe = [], []
    for w in wiersze:
        t = czas(w.get("czas_zdarzenia"))
        if t is None:
            continue
        wpis = {"czas": t, "koszt": liczba(w.get("koszt_zl")), "m3": liczba(w.get("m3")),
                "pct": liczba(w.get("pct")), "uwagi": (w.get("uwagi") or "").strip()}
        if w.get("zdarzenie") == "oproznienie":
            wywozy.append(wpis)
        elif w.get("zdarzenie") == "inwestycja":
            jednorazowe.append(wpis)
    wywozy.sort(key=lambda x: x["czas"])

    # Cykl z kolejnych wierszy, nie z kolumny CSV - działa też dla wpisów dopisanych ręcznie
    for poprzedni, w in zip(wywozy, wywozy[1:]):
        w["cykl_dni"] = (w["czas"].date() - poprzedni["czas"].date()).days
    cykle = [w["cykl_dni"] for w in wywozy if w.get("cykl_dni")]
    sredni_cykl = round(sum(cykle) / len(cykle), 1) if cykle else None

    lata = {}
    for w in wywozy:
        r = lata.setdefault(w["czas"].year, {"rok": w["czas"].year, "wywozy": 0, "koszt_zl": 0, "m3": 0, "bez_kwoty": 0})
        r["wywozy"] += 1
        r["koszt_zl"] += w["koszt"] or 0
        r["m3"] += w["m3"] or 0
        r["bez_kwoty"] += w["koszt"] is None
    for r in lata.values():
        r["koszt_zl"] = round(r["koszt_zl"], 2)

    ostatni_koszt = next((w["koszt"] for w in reversed(wywozy) if w["koszt"]), None)
    m3_wywoz = next((w["m3"] for w in reversed(wywozy) if w["m3"]), None)
    ostatni = wywozy[-1]["czas"] if wywozy else None

    print(json.dumps({
        "liczba_wywozow": len(wywozy),
        "ostatni_wywoz": ostatni.strftime("%d.%m.%Y") if ostatni else None,
        "dni_od_wywozu": (datetime.now().date() - ostatni.date()).days if ostatni else None,
        "sredni_cykl_dni": sredni_cykl,
        "najkrotszy_cykl_dni": min(cykle) if cykle else None,
        "najdluzszy_cykl_dni": max(cykle) if cykle else None,
        "m3_na_dobe": round(m3_wywoz / sredni_cykl, 2) if m3_wywoz and sredni_cykl else None,
        "cena_ostatnia_zl": ostatni_koszt,
        "prognoza_koszt_rok_zl": round(365 / sredni_cykl * ostatni_koszt) if sredni_cykl and ostatni_koszt else None,
        "koszt_wywozow_razem_zl": round(sum(w["koszt"] or 0 for w in wywozy), 2),
        "koszt_jednorazowy_razem_zl": round(sum(j["koszt"] or 0 for j in jednorazowe), 2),
        "lata": [lata[k] for k in sorted(lata, reverse=True)],
        "jednorazowe": [{"data": j["czas"].strftime("%d.%m.%Y"), "koszt": j["koszt"], "opis": j["uwagi"]}
                        for j in sorted(jednorazowe, key=lambda x: x["czas"])],
        "wywozy": [{"data": w["czas"].strftime("%d.%m.%Y"), "cykl_dni": w.get("cykl_dni"), "koszt": w["koszt"],
                    "m3": w["m3"], "pct": w["pct"], "uwagi": w["uwagi"]}
                   for w in reversed(wywozy)][:150],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
