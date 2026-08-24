"""Mannschaften im Feed verwalten – hinzufügen, entfernen, auflisten.

    python -m tools.teams list
    python -m tools.teams add --team 86629 --phase 12647 --label "1. Herren"
    python -m tools.teams remove --team 86629 --phase 12647
    python -m tools.teams remove 2                 # nach Nummer aus `list`

`add` prüft die Angabe direkt gegen die API: existiert die team_id, wie viele Spiele
liegen im Saisonfenster, in welchen Staffeln. Erst danach wird teams.json geschrieben.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter

from src.client import ApiError, HandballNetClient
from src.config import TEAMS_FILE, env, load_setup, save_setup
from src.models import FeedSpec, TeamRef


def _setup_oder_leer() -> tuple[list[TeamRef], list[FeedSpec]]:
    """Beim Anlegen der ersten Mannschaft gibt es teams.json noch nicht."""
    try:
        return load_setup()
    except SystemExit:
        return [], []


def cmd_list() -> int:
    teams, feeds = _setup_oder_leer()
    if not teams:
        print(f"{TEAMS_FILE} ist leer oder fehlt.")
        return 0
    print(f"{len(teams)} Mannschaft(en) in {TEAMS_FILE}:\n")
    for i, t in enumerate(teams, start=1):
        zusatz = f"  [{t.filter_beschreibung}]" if t.filter_beschreibung else ""
        print(f"  {i}. {t.display}   (team_id {t.team_id}){zusatz}")
        print(f"     Feed: {t.feed_slug}.ics")
    if feeds:
        print(f"\n{len(feeds)} Sammel-Feed(s):\n")
        for f in feeds:
            umfang = ", ".join(f.teams) if f.teams else "alle Mannschaften"
            print(f"  {f.slug}.ics   {f.label}   ({umfang})")
    return 0


def cmd_add(args) -> int:
    neu = TeamRef(
        team_id=args.team,
        label=args.label or "",
        slug=args.slug or "",
        phase_ids=list(args.phase or []),
        competition_ids=list(args.competition or []),
    )
    teams, feeds = _setup_oder_leer()
    if any(
        t.team_id == neu.team_id
        and sorted(t.phase_ids) == sorted(neu.phase_ids)
        and sorted(t.competition_ids) == sorted(neu.competition_ids)
        for t in teams
    ):
        print("Diese Mannschaft steht schon in der Konfiguration.")
        return 1

    client = HandballNetClient()
    try:
        rohe = client.matches(
            team_id=neu.team_id,
            date_from=env("SEASON_START", "2026-08-01"),
            date_to=env("SEASON_END", "2027-06-30"),
        )
    except ApiError as exc:
        print(f"API meldet: {exc}", file=sys.stderr)
        return 1
    spiele = [s for s in rohe if neu.passt(s)]

    if not spiele:
        gefunden = (
            f" Ohne Staffel-Filter wären es {len(rohe)}." if rohe and neu.phase_ids else ""
        )
        print(
            "Für diese Angabe liefert die API keine Spiele im Saisonfenster – "
            f"team_id/Staffel prüfen (python -m tools.discover --club <club_id>).{gefunden}",
            file=sys.stderr,
        )
        return 1

    staffeln = Counter((s.get("phase") or {}).get("name", "?") for s in spiele)
    namen = Counter(
        (seite.get("name") or "").strip()
        for s in spiele
        for seite in (s.get("local") or {}, s.get("visitor") or {})
        if seite.get("id") == neu.team_id
    )
    if not neu.label and namen:
        neu.label = namen.most_common(1)[0][0]

    print(f"→ {len(spiele)} Spiele gefunden für {neu.display} (team_id {neu.team_id})")
    for staffel, anzahl in staffeln.most_common():
        print(f"    {staffel}: {anzahl}")
    if len(staffeln) > 1 and not (neu.phase_ids or neu.competition_ids):
        print(
            "    [hinweis] mehrere Staffeln unter einer team_id. Sind das zwei echte\n"
            "    Mannschaften, --phase setzen und zweimal hinzufügen. Gehören sie zusammen\n"
            "    (Liga + Freundschaftsspiele), --phase mehrfach in einem Aufruf angeben.",
            file=sys.stderr,
        )

    teams.append(neu)
    pfad = save_setup(teams, feeds)
    print(f"✓ hinzugefügt in {pfad} · Feed: {neu.feed_slug}.ics")
    return 0


def cmd_remove(args) -> int:
    teams, feeds = _setup_oder_leer()
    if not teams:
        print(f"{TEAMS_FILE} ist leer – nichts zu entfernen.", file=sys.stderr)
        return 1

    if args.nummer is not None:
        if not 1 <= args.nummer <= len(teams):
            print(f"Nummer {args.nummer} gibt es nicht (1…{len(teams)}).", file=sys.stderr)
            return 1
        weg = teams.pop(args.nummer - 1)
    else:
        if args.team is None:
            print("Entweder eine Nummer aus `list` oder --team angeben.", file=sys.stderr)
            return 1
        gesucht = sorted(args.phase or [])
        passend = [
            t
            for t in teams
            if t.team_id == args.team and (not gesucht or sorted(t.phase_ids) == gesucht)
        ]
        if not passend:
            print("Kein Eintrag passt zu dieser Angabe.", file=sys.stderr)
            return 1
        if len(passend) > 1:
            print(
                f"{len(passend)} Einträge passen – bitte --phase ergänzen oder die Nummer "
                "aus `list` verwenden.",
                file=sys.stderr,
            )
            return 1
        weg = passend[0]
        teams.remove(weg)

    # Verwaiste Verweise in den Sammel-Feeds gleich mit aufräumen.
    for f in feeds:
        if weg.feed_slug in f.teams:
            f.teams.remove(weg.feed_slug)
    pfad = save_setup(teams, feeds)
    print(f"✓ entfernt: {weg.display} (team_id {weg.team_id}) – {pfad}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Mannschaften des Feeds verwalten")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="konfigurierte Mannschaften anzeigen")

    a = sub.add_parser("add", help="Mannschaft hinzufügen")
    a.add_argument("--team", type=int, required=True, help="team_id von handball.net")
    a.add_argument(
        "--phase",
        type=int,
        action="append",
        help="Staffel-ID (phase_id); mehrfach angeben für Liga + Freundschaftsspiele",
    )
    a.add_argument(
        "--competition", type=int, action="append", help="Wettbewerbs-ID (competition_id)"
    )
    a.add_argument("--label", help="Anzeigename im Kalendertitel, z.B. „1. Herren“")
    a.add_argument("--slug", help="Dateiname des Einzel-Feeds (Vorgabe: aus dem Label)")

    r = sub.add_parser("remove", help="Mannschaft entfernen")
    r.add_argument("nummer", nargs="?", type=int, help="Nummer aus `list`")
    r.add_argument("--team", type=int, help="team_id")
    r.add_argument(
        "--phase", type=int, action="append", help="Staffel-ID, falls mehrere Einträge passen"
    )

    args = p.parse_args(argv)
    if args.cmd == "list":
        return cmd_list()
    if args.cmd == "add":
        return cmd_add(args)
    return cmd_remove(args)


if __name__ == "__main__":
    raise SystemExit(main())
