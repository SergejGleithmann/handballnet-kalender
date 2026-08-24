"""HTTP-Client für die JSON-API von handball.net.

handball.net läuft seit dem Relaunch auf einem iSquad-Backend. Die SPA spricht mit
`https://www.handball.net/api/new/…` (Proxy auf `handball360.isquad.de/ws/apitribuna`).
Kein Login, kein Token – aber `per_page` ist auf 100 begrenzt, also paginieren.

Wir treten als normaler Client auf: eigener User-Agent-Zusatz, Pause zwischen
Requests, Retry mit Backoff. robots.txt erlaubt `User-Agent: *` – gesperrt sind
dort nur KI-Trainings-Crawler, was auf ein privates Kalender-Tool nicht zutrifft.
"""
from __future__ import annotations

import time

import requests

BASE = "https://www.handball.net/api/new"
SITE = "https://www.handball.net/"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 "
    "handballnet-kalender/1.0 (privates Kalender-Abo)"
)
POLITE_DELAY_S = 0.4
MAX_PER_PAGE = 100
RETRIES = 3


class ApiError(RuntimeError):
    pass


class HandballNetClient:
    def __init__(self, *, delay: float = POLITE_DELAY_S):
        self.delay = delay
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
                "Accept-Language": "de-DE,de;q=0.9",
                "Referer": SITE,
            }
        )
        self._last_request = 0.0

    # ------------------------------------------------------------------ intern
    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request = time.monotonic()

    def get(self, path: str, params: dict | None = None) -> dict:
        """GET auf einen API-Pfad. Wirft ApiError, wenn die API `success: false` meldet."""
        clean = {k: v for k, v in (params or {}).items() if v is not None and v != ""}
        last_exc: Exception | None = None

        for versuch in range(1, RETRIES + 1):
            self._throttle()
            try:
                resp = self.session.get(f"{BASE}/{path.lstrip('/')}", params=clean, timeout=30)
            except requests.RequestException as exc:
                last_exc = exc
            else:
                if resp.status_code >= 500:
                    last_exc = ApiError(f"HTTP {resp.status_code} für {path}")
                else:
                    payload = resp.json()
                    if payload.get("success") is False:
                        fehler = payload.get("error", {})
                        raise ApiError(
                            f"{fehler.get('code', 'FEHLER')}: {fehler.get('message', resp.text[:200])}"
                        )
                    return payload
            if versuch < RETRIES:
                time.sleep(self.delay * 4 * versuch)

        raise ApiError(f"{path} nach {RETRIES} Versuchen fehlgeschlagen: {last_exc}")

    def get_all(self, path: str, params: dict | None = None) -> list[dict]:
        """Wie get(), aber über alle Seiten hinweg (per_page=100)."""
        seiten_params = dict(params or {})
        seiten_params["per_page"] = MAX_PER_PAGE
        items: list[dict] = []
        page = 1

        while True:
            seiten_params["page"] = page
            payload = self.get(path, seiten_params)
            items.extend(payload.get("data") or [])
            pag = payload.get("pagination") or {}
            last = pag.get("last_page")
            if not last or page >= last:
                break
            page += 1

        return items

    # ------------------------------------------------------------------ API
    def matches(
        self,
        *,
        team_id: int | None = None,
        club_id: int | None = None,
        phase_id: int | None = None,
        competition_id: int | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
    ) -> list[dict]:
        """Spiele. Mindestens ein Filter ist Pflicht (sagt die API selbst)."""
        return self.get_all(
            "matches",
            {
                "team_id": team_id,
                "club_id": club_id,
                "phase_id": phase_id,
                "competition_id": competition_id,
                "date_from": date_from,
                "date_to": date_to,
            },
        )

    def clubs(self) -> list[dict]:
        """Das komplette Vereinsverzeichnis (rund 4400 Einträge, 45 Requests).

        Die API ignoriert Suchparameter, also filtern wir lokal – deshalb ein Aufruf,
        dessen Ergebnis die Discovery zwischenspeichert.
        """
        return self.get_all("teams/clubs")
