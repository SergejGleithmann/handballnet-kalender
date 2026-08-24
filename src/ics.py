"""Spiele -> eine VCALENDAR (.ics) zum Abonnieren."""
from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from icalendar import Calendar, Event as VEvent

from src.models import Match

_ABGESETZT = {"Abgesetzt", "Annulliert"}


def build_calendar(
    matches: list[Match], *, calendar_name: str, timezone: str, duration_min: int = 120
) -> Calendar:
    tz = ZoneInfo(timezone)
    cal = Calendar()
    cal.add("prodid", "-//handballnet-kalender//Spielplan//DE")
    cal.add("version", "2.0")
    cal.add("x-wr-calname", calendar_name)
    cal.add("x-wr-timezone", timezone)
    cal.add("method", "PUBLISH")

    for m in sorted(matches, key=lambda x: x.start):
        ve = VEvent()
        ve.add("uid", m.uid)
        ve.add("summary", m.summary)

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
        # DTSTAMP deterministisch aus dem Anwurf: gleiche Daten -> gleiche Datei,
        # damit der Actions-Lauf keine Pseudo-Änderungen produziert.
        ve.add("dtstamp", m.start.replace(tzinfo=tz))
        cal.add_component(ve)

    return cal


def to_ics_bytes(cal: Calendar) -> bytes:
    return cal.to_ical()
