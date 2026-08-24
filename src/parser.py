"""Spiel-JSON der API in `Match`-Objekte übersetzen – inklusive der drei Fallstricke.

1. **Zeitzone**: `date` kommt als `"2026-09-12T17:00:00+00:00"`, ist aber Ortszeit.
   Der Offset ist gelogen. Wir lesen deshalb nur `YYYY-MM-DDTHH:MM` und behandeln
   das als Ortszeit – genauso macht es die Website in ihrem eigenen ICS-Writer.
   (Der versteckte Team-Feed von handball.net macht es falsch und liegt 1–2 h daneben.)
2. **Spiele ohne Anwurfzeit** stehen als `00:00` → ganztägiger Termin.
3. **Dubletten**: dieselbe Begegnung erscheint teils zweimal mit verschiedener `id`.
   Wir deduplizieren über (Datum, Heim-Team-ID, Gast-Team-ID).
"""
from __future__ import annotations

import re
from datetime import datetime

from src.models import Match, TeamRef

_DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2})")

# Die API liefert Status auf Spanisch (iSquad-Backend).
STATUS_DE = {
    "Pendiente": "Angesetzt",
    "Finalizado": "Beendet",
    "En juego": "Läuft",
    "Aplazado": "Verlegt",
    "Suspendido": "Abgesetzt",
    "Anulado": "Annulliert",
}


def parse_local_datetime(raw: str | None) -> datetime | None:
    """Naiv parsen: der Zeitzonen-Offset der API ist nicht zu gebrauchen."""
    if not raw:
        return None
    m = _DATE_RE.match(str(raw).strip())
    if not m:
        return None
    jahr, monat, tag, stunde, minute = (int(g) for g in m.groups())
    return datetime(jahr, monat, tag, stunde, minute)


# --------------------------------------------------------------- Groß-/Kleinschreibung
# Vereinsnamen und Adressen kommen aus dem Backend teils komplett in Großbuchstaben,
# und die Umlaute sind dabei klein geblieben ("ANFAHRT üBER FEAUXWEG"). Im Kalender
# liest sich das schlecht, also normalisieren wir – aber nur, wenn der Text *keinen*
# Kleinbuchstaben enthält, damit gepflegte Namen ("Ahlener SG 93 e.V. II") bleiben.
_ABKUERZUNGEN = {
    "TV", "TSV", "TUS", "SG", "SV", "SVE", "HSG", "JSG", "MTV", "BSV", "ASC", "OSC",
    "DJK", "SC", "FC", "HC", "HSC", "TG", "TSG", "KSV", "HVE", "SPH", "SH", "GSH",
    "THW", "AHC", "ATV", "MSV", "PSV", "RSV", "TBV", "BV", "LG", "VT", "HK", "II", "III",
}
_SCHREIBWEISE = {
    "VFL": "VfL", "VFB": "VfB", "TUS": "TuS", "E.V.": "e.V.", "E. V.": "e. V.",
    # Adress-Kürzel: ohne Vokal, aber keine Vereinskürzel.
    "STR": "Str", "NR": "Nr", "PL": "Pl",
}
_ROEMISCH = {"I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"}
_VOKALE = set("AEIOUÄÖÜ")


def _ist_abkuerzung(wort: str) -> bool:
    """Kürzel, die groß bleiben sollen.

    Neben der Liste greift eine Faustregel: bis zu vier Buchstaben ohne Vokal ist im
    Deutschen kein Wort, sondern ein Kürzel – fängt TB, MTC, SPH und den langen
    Rattenschwanz weiterer Vereinskürzel ab, ohne sie alle aufzählen zu müssen.
    """
    if wort in _ABKUERZUNGEN or wort in _ROEMISCH:
        return True
    return wort.isalpha() and len(wort) <= 4 and not set(wort) & _VOKALE


def tidy_caps(text: str) -> str:
    if not text or _hat_kleinbuchstaben(text):
        return text
    woerter = []
    for wort in text.split(" "):
        # "E.V." trägt den Punkt selbst, "HSG," einen angehängten – beide Formen prüfen.
        for kandidat in (wort, wort.strip(".,;:")):
            gross = kandidat.upper()
            if gross in _SCHREIBWEISE:
                woerter.append(wort.replace(kandidat, _SCHREIBWEISE[gross]))
                break
            if _ist_abkuerzung(gross):
                woerter.append(wort)
                break
        else:
            woerter.append(wort.title())
    return " ".join(woerter)


def _hat_kleinbuchstaben(text: str) -> bool:
    """Nur ASCII-Kleinbuchstaben zählen.

    Die Großschreibung im Backend erfasst Umlaute nicht ("GRüNSTRAßE"), ein kleines
    ü oder ß ist also kein Zeichen für gepflegte Schreibweise.
    """
    return any("a" <= c <= "z" for c in text)


def _teamname(seite: dict | None) -> str:
    if not seite:
        return "unbekannt"
    return tidy_caps((seite.get("name") or "").strip()) or f"Team {seite.get('id')}"


def _liga(phase: dict) -> str:
    """Sprechender Ligenname aus Staffel und Wettbewerb.

    Meist trägt die Staffel die Information ("HVW Männer Oberliga Staffel 2"). Manche
    heißen aber nur nach ihrer Gruppe ("Mitte") – dann erst sagt der Wettbewerb, worum
    es geht ("3. Liga Frauen"). Wir hängen ihn nur davor, wenn der Staffelname deutlich
    kürzer ist, sonst doppelt es sich ("KÜS Bezirksligen Männer · KÜS Bezirksliga Mitte").
    """
    staffel = (phase.get("name") or "").strip()
    wettbewerb = ((phase.get("competition") or {}).get("name") or "").strip()
    if not wettbewerb:
        return staffel
    if not staffel:
        return wettbewerb
    if len(staffel) < 0.6 * len(wettbewerb):
        return f"{wettbewerb} · {staffel}"
    return staffel


def _venue(spiel: dict) -> tuple[str, str]:
    feld = spiel.get("field") or {}
    anlage = feld.get("installation") or {}
    name = (feld.get("name") or anlage.get("name") or "").strip()
    adresse = (anlage.get("address") or "").strip()
    return tidy_caps(name), tidy_caps(adresse)


def _result(spiel: dict) -> str:
    """Ergebnis als "28:24" – oder leer.

    Beendete Spiele ohne gemeldetes Ergebnis stehen in der API als 0:0 (gesehen bei
    Freundschaftsspielen). Das schreiben wir nicht in den Titel, sonst behauptet der
    Kalender ein 0:0, das es nie gab.
    """
    res = spiel.get("result") or {}
    heim, gast = res.get("local"), res.get("visitor")
    if heim is None or gast is None:
        return ""
    if not int(heim) and not int(gast):
        return ""
    return f"{heim}:{gast}"


def to_match(spiel: dict, team: TeamRef) -> Match | None:
    """Ein API-Spiel in ein `Match` übersetzen. None, wenn kein Datum lesbar ist."""
    start = parse_local_datetime(spiel.get("date"))
    if start is None:
        return None

    heim, gast = spiel.get("local") or {}, spiel.get("visitor") or {}
    phase = spiel.get("phase") or {}
    status = spiel.get("status") or {}
    venue, adresse = _venue(spiel)

    return Match(
        team=team,
        id=int(spiel["id"]),
        start=start,
        all_day=(start.hour == 0 and start.minute == 0),
        home=_teamname(heim),
        away=_teamname(gast),
        is_home=(heim.get("id") == team.team_id),
        league=_liga(phase),
        round=spiel.get("round"),
        status=STATUS_DE.get(status.get("name", ""), status.get("name", "") or ""),
        finished=bool(status.get("is_finished")),
        result=_result(spiel) if status.get("is_finished") else "",
        venue=venue,
        address=adresse,
        referees=[
            f"{(r.get('first_name') or '').strip()} {(r.get('last_name') or '').strip()}".strip()
            for r in (spiel.get("referees") or [])
            if r.get("role_position") in (1, 2)
        ],
        dedupe_key=(start, heim.get("id"), gast.get("id")),
    )


def dedupe(matches: list[Match]) -> list[Match]:
    """Doppelte Spiele entfernen – erst über die UID, dann über die Begegnung."""
    gesehen_uid: set[str] = set()
    gesehen_paarung: set[tuple] = set()
    raus: list[Match] = []

    for m in sorted(matches, key=lambda x: (x.start, x.id)):
        if m.uid in gesehen_uid or m.dedupe_key in gesehen_paarung:
            continue
        gesehen_uid.add(m.uid)
        gesehen_paarung.add(m.dedupe_key)
        raus.append(m)

    return raus
