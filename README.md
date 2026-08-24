# handballnet-kalender · Mannschafts-Spielpläne als Kalender-Abo

Baut aus den Spielplänen von **handball.net** Kalender-Abos (ICS) für **frei wählbare
Mannschaften** – je Mannschaft einen Feed, dazu beliebige Zusammenstellungen. Mit Halle
und Adresse im Termin, Gegner und Staffel im Titel, Ergebnis nach dem Spiel. Ein
GitHub-Actions-Job baut die Feeds regelmäßig neu und veröffentlicht sie über GitHub Pages.

## Warum überhaupt

handball.net kann seit dem Relaunch **keinen Mannschafts-Spielplan mehr abonnieren**.
Die Kalender-Funktion der Seite kennt nur zwei Fälle: eine **ganze Liga** oder ein
**einzelnes Spiel**. Auf der Team- und der Spielplan-Seite gibt es keinen Kalender-Knopf,
und die alte Ankündigung des Kalender-Exports ist heute eine 404-Seite.

Auf dem Backend liegt zwar ein nirgends verlinkter Team-Feed
(`json/equipo_google_calendar.php?id_equipo=…`), der ist als Abo aber unbrauchbar:

- **Die UIDs wechseln bei jedem Abruf** → jede Aktualisierung legt neue Termine an,
  statt bestehende zu ändern.
- **Die Zeiten liegen 1–2 Stunden daneben**: Ortszeit wird als UTC ausgegeben
  (`DTSTART:20260912T170000Z` für einen Anwurf um 17:00).
- `LOCATION` ist leer, `DESCRIPTION` enthält `Pendiente`, `X-WR-TIMEZONE` ist
  `Europe/Madrid`.

Dieses Tool nutzt stattdessen die JSON-API der Seite und schreibt den ICS selbst –
mit stabilen UIDs, korrekter Zeitzone und der Halle im Termin.

## Datenquelle

`https://www.handball.net/api/new/…` – die API, mit der die Website selbst arbeitet
(Proxy auf das iSquad-Backend). **Kein Login, kein Token.** Genutzt werden:

| Endpunkt | Zweck |
|---|---|
| `GET /matches?team_id=…&date_from=…&date_to=…` | Spiele einer Mannschaft |
| `GET /matches?club_id=…` | alle Mannschaften eines Vereins (für die Discovery) |
| `GET /teams/clubs` | Vereinsverzeichnis (~4400 Einträge, paginiert) |

`per_page` ist auf 100 begrenzt, der Client paginiert selbst. robots.txt erlaubt
`User-agent: *`; gesperrt sind dort nur KI-Trainings-Crawler. Der Client tritt mit
eigenem User-Agent auf, pausiert zwischen Requests und läuft alle sechs Stunden.

## Lokal einrichten

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # Saisonfenster, Kalendername, Zeitzone
```

## Mannschaften wählen

Mannschaften stehen in **`teams.json`** und lassen sich jederzeit hinzufügen oder
entfernen – von Hand oder über `tools.teams`.

```bash
# 1) Verein suchen (lädt das Vereinsverzeichnis einmalig nach .cache/)
python -m tools.discover Villigst

# 2) Mannschaften des Vereins mit team_id und Staffel-IDs auflisten
python -m tools.discover --club 4868

# 3) hinzufügen / anzeigen / entfernen
python -m tools.teams add --team 86629 --phase 12647 --label "1. Herren"
python -m tools.teams list
python -m tools.teams remove 2            # Nummer aus `list`
```

`add` prüft die Angabe vor dem Speichern gegen die API und zeigt, wie viele Spiele in
welchen Staffeln gefunden wurden.

### Warum es die Staffel-ID braucht

handball.net führt unter **einer** `team_id` mitunter **zwei tatsächliche Mannschaften**
eines Vereins. Beispiel HVE Villigst-Ergste: `team_id 86629` hat 26 Spiele in der
HVW Männer Oberliga Staffel 2 **und** 22 in der KÜS Bezirksliga Mitte – am selben
Wochenende, teils zur selben Zeit. Das sind zwei Teams unter einem Datensatz. Erst
`--phase` trennt sie:

```json
{
  "teams": [
    { "label": "1. Herren", "team_id": 86629, "phase_ids": [12647] },
    { "label": "2. Herren", "team_id": 86629, "phase_ids": [14340] }
  ]
}
```

Gehören mehrere Staffeln **zusammen** (Liga plus Freundschaftsspiele derselben
Mannschaft), kommen sie in einen Eintrag: `--phase 12647 --phase 15539`.
Ohne Staffel-Angabe landet alles im Kalender, was die API unter der `team_id` führt.

## Ein Feed je Mannschaft – plus Sammel-Feeds

**Jede Mannschaft bekommt automatisch ihren eigenen Feed** (`docs/<slug>.ics`). Wer nur
eine davon im Kalender will, abonniert nur die eine; wer eine loswerden will, bestellt
sie ab. Das ist der flexible Weg – dafür braucht es keine zweite Konfiguration.

Zusätzlich lassen sich unter `feeds` benannte Zusammenstellungen definieren, wenn man
mehrere Mannschaften mit **einer** URL weitergeben möchte:

```json
{
  "teams": [
    { "label": "HVE 1. Herren", "team_id": 86629, "slug": "hve-1-herren", "phase_ids": [12647] },
    { "label": "HVE 2. Herren", "team_id": 86629, "slug": "hve-2-herren", "phase_ids": [14340] },
    { "label": "PSV Recklinghausen Damen", "team_id": 87292, "slug": "psv-damen", "phase_ids": [14058] }
  ],
  "feeds": [
    { "label": "Alle Mannschaften", "slug": "alle" },
    { "label": "Ohne HVE 1. Herren", "slug": "ohne-hve-1-herren",
      "teams": ["hve-2-herren", "psv-damen"] }
  ]
}
```

Ein Feed ohne `teams` umfasst alle Mannschaften. Fehlt die `feeds`-Liste ganz, entsteht
automatisch ein Feed `alle.ics`. Tippfehler in `teams` melden sich beim Lauf, statt still
einen leeren Kalender zu bauen. `slug` ist optional – ohne Angabe wird er aus dem Label
abgeleitet.

## Feed bauen

```bash
python -m src.main
# -> je Mannschaft eine docs/<slug>.ics, dazu die Sammel-Feeds und docs/index.html
```

Der Lauf braucht einen Request pro Mannschaft (plus Pagination) und ist in Sekunden
durch. `DTSTAMP` und `LAST-MODIFIED` tragen den Zeitpunkt des Laufs – daran erkennen
Kalender-Clients, dass eine Fassung neuer ist als die gespeicherte. Die Dateien in
`docs/` ändern sich deshalb bei jedem Lauf, auch wenn sich an den Spielen nichts getan
hat.

## Automatisch veröffentlichen

1. Repo zu GitHub pushen.
2. **Settings → Pages → Source: GitHub Actions**.
3. Workflow „Build & publish handball.net ICS" einmal manuell starten.
4. Abo-URLs: `https://<user>.github.io/<repo>/<slug>.ics`
   Übersicht mit allen Feeds: `https://<user>.github.io/<repo>/`

Secrets braucht der Job keine – die Daten sind öffentlich. Optionale **Variables**:
`SEASON_START`, `SEASON_END`, `TIMEZONE`, `CALENDAR_NAME`, `MATCH_DURATION_MIN`.
Ein Push auf `main` (z.B. geändertes `teams.json`) baut die Feeds sofort neu, ansonsten
läuft der Job alle sechs Stunden.

## Kalender abonnieren

- **Apple Kalender:** Ablage → Neues Kalenderabonnement → Abo-URL einfügen.
- **Google Kalender:** Andere Kalender → Per URL → Abo-URL einfügen.
  (Google aktualisiert Abos nur alle paar Stunden – normal.)

## Was im Termin steht

```
SUMMARY:     [1. Herren] HSG Gevelsberg Silschede – HVE Villigst-Ergste II
LOCATION:    SPH Gevelsberg-West, Am Hofe 10, 58285 Gevelsberg
DESCRIPTION: HVW Männer Oberliga Staffel 2
             Auswärtsspiel · Spieltag 2
             Schiedsrichter: Serkan Kahraman, Markus Schürhoff
             https://www.handball.net/match/368939
DTSTART;TZID=Europe/Berlin:20260912T193000     (Dauer 2 h, einstellbar)
UID:         match-368939@handball.net          (stabil -> Verlegung = Update)
```

Ist das Spiel gespielt, steht das Ergebnis hinten im Titel. Abgesetzte Spiele bekommen
`STATUS:CANCELLED`, Kalender-Apps streichen sie durch.

## Was Kalender-Clients uns abverlangt haben

- **Keine `VTIMEZONE` → Google lehnt ab.** Wer per `TZID` auf eine Zeitzone verweist,
  muss sie im Kalender definieren (RFC 5545). Apple kennt die Olson-Namen und verzeiht
  das Fehlen, Google nicht.
- **Die `VTIMEZONE` muss die kanonische `RRULE`-Form haben.** `Timezone.from_tzid` der
  `icalendar`-Bibliothek erzeugt stattdessen `RDATE`-Listen, einen `COMMENT` und einen
  Block mit identischen Offsets; damit kam Google nicht zurecht. Wir schreiben die
  EU-Regel (letzter Sonntag im März bzw. Oktober) selbst – dieselbe Form, die auch der
  Liga-Feed von handball.net ausliefert.
- **Kein `METHOD:PUBLISH`.** Mit `METHOD` liest Google die Datei als iTIP-Nachricht
  (Einladung) statt als abonnierbaren Kalender.
- **`DTSTAMP` gehört auf den Zeitpunkt des Erzeugens**, nicht auf den Anwurf. Sonst
  bliebe der Wert bei einem Hallenwechsel unverändert und die Änderung käme bei den
  Clients nicht an.

## Eigenheiten der API, die hier abgefangen werden

1. **Der Zeitstempel lügt.** `date` kommt als `"2026-09-12T17:00:00+00:00"`, ist aber
   Ortszeit. Wir lesen nur `YYYY-MM-DDTHH:MM` und behandeln es als `Europe/Berlin` –
   genauso macht es die Website in ihrem eigenen ICS-Writer.
2. **Spiele ohne Anwurfzeit** stehen als `00:00` → ganztägiger Termin statt Mitternacht.
3. **Dubletten**: dieselbe Begegnung erscheint teils zweimal mit verschiedener `id` →
   dedupliziert über (Datum, Heim-ID, Gast-ID).
4. **0:0 bei beendeten Spielen** heißt „kein Ergebnis gemeldet" und wird nicht als
   Ergebnis geschrieben.
5. **Großbuchstaben**: Namen und Adressen kommen teils komplett groß, die Umlaute
   dabei klein (`GRüNSTRAßE`) → normalisiert, gepflegte Schreibweisen bleiben.
6. **Nur die laufende Saison** liegt in der API; ältere Spielzeiten liefern nichts.

## Projektstruktur

| Datei | Zweck |
|---|---|
| `src/client.py` | HTTP-Client: Pagination, Retry, Pause zwischen Requests |
| `src/parser.py` | Spiel-JSON → `Match` (Zeitzone, ganztägig, Dedupe, Schreibweise) |
| `src/models.py` | `TeamRef` (Mannschaft), `FeedSpec` (Sammel-Feed) und `Match` |
| `src/ics.py` | `Match`-Liste → eine VCALENDAR |
| `src/dashboard.py` | statisches `docs/index.html` mit allen Feeds, nach Mannschaft gruppiert |
| `src/config.py` | `.env` + `teams.json` laden und schreiben |
| `src/main.py` | Orchestrierung |
| `tools/discover.py` | Vereine suchen, Mannschaften mit Staffel-IDs auflisten |
| `tools/teams.py` | `add` / `list` / `remove` für `teams.json` |
| `.github/workflows/build-ics.yml` | Cron-Job + Pages-Deploy |

## Verwandt

`~/Documents/Projects/spieler+` macht dasselbe für **SpielerPlus** (Trainings und
Termine, gefiltert auf die eigene Zusage). Bewusst getrennte Feeds: getrennt
abbestellbar, eigene Kalenderfarbe, und ein Fehler in der einen Quelle legt die andere
nicht lahm. Achtung: SpielerPlus führt Spiele oft ebenfalls als Termin – wer beide
Kalender abonniert, sieht Spiele zweimal.
