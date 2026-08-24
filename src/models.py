"""Datenmodelle: konfigurierte Mannschaft und ein einzelnes Spiel."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime

MATCH_URL = "https://www.handball.net/match/{id}"


def slugify(text: str, fallback: str = "team") -> str:
    """„HVE 1. Herren" -> „hve-1-herren". Wird zum Dateinamen des Feeds."""
    ohne_umlaut = (
        text.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    )
    ascii_text = unicodedata.normalize("NFKD", ohne_umlaut).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug or fallback


@dataclass
class TeamRef:
    """Eine in teams.json konfigurierte Mannschaft.

    `phase_ids` (Staffeln) und `competition_ids` (Wettbewerbe) sind optional, aber oft
    nötig: handball.net führt unter *einer* team_id mitunter zwei tatsächliche
    Mannschaften eines Vereins (z.B. Oberliga- und Bezirksliga-Herren). Erst der
    Staffel-Filter trennt sie. Mehrere Staffeln sind erlaubt, damit Liga *und*
    Freundschaftsspiele derselben Mannschaft in einen Eintrag passen.
    Leere Listen heißen: alles nehmen, was die API unter der team_id führt.
    """

    team_id: int
    label: str = ""
    slug: str = ""                  # Dateiname des Einzel-Feeds; leer = aus dem Label
    phase_ids: list[int] = field(default_factory=list)
    competition_ids: list[int] = field(default_factory=list)

    @property
    def display(self) -> str:
        return self.label or f"Team {self.team_id}"

    @property
    def feed_slug(self) -> str:
        return self.slug or slugify(self.display, fallback=f"team-{self.team_id}")

    @property
    def filter_beschreibung(self) -> str:
        teile = []
        if self.phase_ids:
            teile.append("Staffel " + ", ".join(str(p) for p in self.phase_ids))
        if self.competition_ids:
            teile.append("Wettbewerb " + ", ".join(str(c) for c in self.competition_ids))
        return ", ".join(teile)

    def passt(self, spiel: dict) -> bool:
        """Gehört dieses API-Spiel zu dieser konfigurierten Mannschaft?"""
        if not self.phase_ids and not self.competition_ids:
            return True
        phase = spiel.get("phase") or {}
        if self.phase_ids and phase.get("id") in self.phase_ids:
            return True
        if self.competition_ids and (phase.get("competition") or {}).get(
            "id"
        ) in self.competition_ids:
            return True
        return False

    def as_dict(self) -> dict:
        d: dict = {"label": self.label, "team_id": self.team_id}
        if self.slug:
            d["slug"] = self.slug
        if self.phase_ids:
            d["phase_ids"] = self.phase_ids
        if self.competition_ids:
            d["competition_ids"] = self.competition_ids
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "TeamRef":
        return cls(
            team_id=int(d["team_id"]),
            label=str(d.get("label", "") or ""),
            slug=str(d.get("slug", "") or ""),
            phase_ids=_ids(d.get("phase_ids"), d.get("phase_id")),
            competition_ids=_ids(d.get("competition_ids"), d.get("competition_id")),
        )


@dataclass
class FeedSpec:
    """Ein zusätzlicher Sammel-Feed über mehrere Mannschaften.

    `teams` enthält die Slugs der Mannschaften; leer heißt „alle".
    """

    label: str
    slug: str
    teams: list[str] = field(default_factory=list)

    def gilt_fuer(self, team: "TeamRef") -> bool:
        return not self.teams or team.feed_slug in self.teams

    def as_dict(self) -> dict:
        d: dict = {"label": self.label, "slug": self.slug}
        if self.teams:
            d["teams"] = self.teams
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "FeedSpec":
        label = str(d.get("label", "") or "Alle Mannschaften")
        return cls(
            label=label,
            slug=str(d.get("slug", "") or slugify(label, fallback="feed")),
            teams=[str(t) for t in (d.get("teams") or [])],
        )


def _ids(liste, einzel) -> list[int]:
    """Akzeptiert `phase_ids: [1,2]` genauso wie die Kurzform `phase_id: 1`."""
    if liste:
        return [int(x) for x in liste]
    if einzel is not None:
        return [int(einzel)]
    return []


@dataclass
class Match:
    """Ein Spiel, schon aufbereitet: Zeit als Ortszeit, Halle mit Adresse, Ergebnis."""

    team: TeamRef
    id: int
    start: datetime                 # naiv = Ortszeit (siehe parser.py)
    all_day: bool = False           # Spiele ohne Anwurfzeit stehen in der API als 00:00
    home: str = ""
    away: str = ""
    is_home: bool = False
    league: str = ""                # Staffelname, z.B. "HVW Männer Oberliga Staffel 2"
    round: int | None = None        # Spieltag
    status: str = ""                # Rohstatus der API (spanisch), s. STATUS_DE
    finished: bool = False
    result: str = ""                # "28:24", sonst leer
    venue: str = ""                 # Hallenname
    address: str = ""               # Straße, PLZ Ort
    referees: list[str] = field(default_factory=list)
    dedupe_key: tuple = ()          # (Datum, Heim-ID, Gast-ID) – gegen Doppeleinträge

    @property
    def uid(self) -> str:
        """Stabile UID: Verlegungen werden zum Update, nicht zum zweiten Termin."""
        return f"match-{self.id}@handball.net"

    @property
    def url(self) -> str:
        return MATCH_URL.format(id=self.id)

    @property
    def summary(self) -> str:
        paarung = f"{self.home} – {self.away}"
        titel = f"[{self.team.display}] {paarung}"
        return f"{titel} {self.result}".strip() if self.result else titel

    @property
    def location(self) -> str:
        parts = [p for p in (self.venue, self.address) if p]
        return ", ".join(parts)

    @property
    def description(self) -> str:
        zeilen = [self.league] if self.league else []
        detail = ["Heimspiel" if self.is_home else "Auswärtsspiel"]
        if self.round is not None:
            detail.append(f"Spieltag {self.round}")
        if self.result:
            detail.append(f"Ergebnis {self.result}")
        zeilen.append(" · ".join(detail))
        if self.referees:
            zeilen.append("Schiedsrichter: " + ", ".join(self.referees))
        zeilen.append(self.url)
        return "\n".join(zeilen)
