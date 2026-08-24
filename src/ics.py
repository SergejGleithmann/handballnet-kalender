"""Spiele -> eine VCALENDAR (.ics) zum Abonnieren."""
from __future__ import annotations

# Alias, weil der Parameter `timezone` hier die Zeitzone als Name trägt.
from datetime import datetime, timedelta, timezone as dt_timezone
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event as VEvent, Timezone

from src.models import Match

_ABGESETZT = {"Abgesetzt", "Annulliert"}

# Handgeschriebene VTIMEZONE in der kanonischen RRULE-Form (EU-Sommerzeitregel:
# letzter Sonntag im März bzw. Oktober). `Timezone.from_tzid` erzeugt stattdessen
# RDATE-Listen, einen COMMENT und einen Block mit identischen Offsets – Google
# kommt damit nicht zurecht. Genau diese Form liefert auch der Liga-Feed von
# handball.net aus, der für Google gebaut ist.
_EU_TIMEZONES = {
    "Europe/Berlin": ("CET", "CEST", "+0100", "+0200"),
    "Europe/Vienna": ("CET", "CEST", "+0100", "+0200"),
    "Europe/Zurich": ("CET", "CEST", "+0100", "+0200"),
}


def _vtimezone(timezone: str, matches: list[Match]) -> Timezone | None:
    eu = _EU_TIMEZONES.get(timezone)
    if eu:
        standard, sommer, offset_winter, offset_sommer = eu
        return Timezone.from_ical(
            "BEGIN:VTIMEZONE\r\n"
            f"TZID:{timezone}\r\n"
            f"X-LIC-LOCATION:{timezone}\r\n"
            "BEGIN:DAYLIGHT\r\n"
            f"TZOFFSETFROM:{offset_winter}\r\n"
            f"TZOFFSETTO:{offset_sommer}\r\n"
            f"TZNAME:{sommer}\r\n"
            "DTSTART:19700329T020000\r\n"
            "RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=-1SU\r\n"
            "END:DAYLIGHT\r\n"
            "BEGIN:STANDARD\r\n"
            f"TZOFFSETFROM:{offset_sommer}\r\n"
            f"TZOFFSETTO:{offset_winter}\r\n"
            f"TZNAME:{standard}\r\n"
            "DTSTART:19701025T030000\r\n"
            "RRULE:FREQ=YEARLY;BYMONTH=10;BYDAY=-1SU\r\n"
            "END:STANDARD\r\n"
            "END:VTIMEZONE\r\n"
        )
    if not matches:
        return None
    # Andere Zeitzonen: aus der Zeitzonendatenbank ableiten.
    return Timezone.from_tzid(
        timezone,
        first_date=min(m.start for m in matches).date() - timedelta(days=400),
        last_date=max(m.start for m in matches).date() + timedelta(days=400),
    )


def build_calendar(
    matches: list[Match],
    *,
    calendar_name: str,
    timezone: str,
    duration_min: int = 120,
    gebaut_am: datetime | None = None,
    mit_team: bool = False,
) -> Calendar:
    tz = ZoneInfo(timezone)
    stempel = gebaut_am or datetime.now(dt_timezone.utc)
    cal = Calendar()
    cal.add("prodid", "-//handballnet-kalender//Spielplan//DE")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", calendar_name)
    cal.add("x-wr-timezone", timezone)
    # Kein METHOD: mit METHOD:PUBLISH liest Google die Datei als iTIP-Nachricht
    # (Einladung) statt als abonnierbaren Kalender und lehnt sie ab.

    # Wer per TZID auf eine Zeitzone verweist, muss sie im Kalender auch definieren
    # (RFC 5545). Apple kennt die Olson-Namen und verzeiht das Fehlen, strengere
    # Clients lehnen den Feed sonst ab.
    tz_component = _vtimezone(timezone, matches)
    if tz_component is not None:
        cal.add_component(tz_component)

    for m in sorted(matches, key=lambda x: x.start):
        ve = VEvent()
        ve.add("uid", m.uid)
        ve.add("summary", m.summary(mit_team=mit_team))

        if m.all_day:
            # Spiel ohne Anwurfzeit: ganztägig, statt einen Termin um Mitternacht zu erfinden.
            ve.add("dtstart", m.start.date())
            ve.add("dtend", (m.start + timedelta(days=1)).date())
        else:
            start = m.start.replace(tzinfo=tz)
            ve.add("dtstart", start)
            ve.add("dtend", start + timedelta(minutes=duration_min))

        if m.location:
            ve.add("location", m.location)
        ve.add("description", m.description)
        ve.add("url", m.url)
        # Abgesetzte Spiele als CANCELLED markieren – Kalender-Apps streichen sie dann durch.
        ve.add("status", "CANCELLED" if m.status in _ABGESETZT else "CONFIRMED")
        if m.status:
            ve.add("x-handballnet-status", m.status)
        # DTSTAMP ist der Zeitpunkt des Erzeugens, nicht der des Anwurfs. Clients
        # entscheiden daran, ob eine Fassung neuer ist als die gespeicherte – stünde
        # hier die Anwurfzeit, bliebe der Wert bei einem Hallenwechsel unverändert
        # und die Änderung käme nicht an.
        ve.add("dtstamp", stempel)
        ve.add("last-modified", stempel)
        cal.add_component(ve)

    return cal


def to_ics_bytes(cal: Calendar) -> bytes:
    return cal.to_ical()
