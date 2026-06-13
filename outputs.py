"""
outputs.py
==========
Presentation layer. Turns a SolveResult into the various views an institution
needs: per-section grid, per-faculty grid + load, per-room utilisation, a
master grid, a validation report, plus JSON/CSV/ICS exporters.

The presentation layer never re-derives constraints; it only formats the
already-validated placements (clean separation of concerns).
"""
from __future__ import annotations

import csv
import io
import json
from collections import defaultdict
from typing import Dict, List

from models import Institute


def _empty_grid(days: List[str], periods: int) -> List[List[str]]:
    return [["" for _ in range(periods)] for _ in days]


def section_grids(inst: Institute, placements: List[dict]) -> Dict[str, list]:
    days = inst.calendar.working_days
    P = inst.calendar.periods_per_day
    grids: Dict[str, list] = {sid: _empty_grid(days, P) for sid in inst.sections}
    for pl in placements:
        d = days.index(pl["day"])
        label = f'{pl["course"]} ({pl["kind"]})\n{pl["room"]}/{pl["faculty"]}'
        grids[pl["section"]][d][pl["period"]] = label
    return grids


def faculty_grids(inst: Institute, placements: List[dict]) -> Dict[str, dict]:
    days = inst.calendar.working_days
    P = inst.calendar.periods_per_day
    out: Dict[str, dict] = {}
    load = defaultdict(int)
    grids = {fid: _empty_grid(days, P) for fid in inst.faculty}
    for pl in placements:
        f = pl["faculty"]
        if f not in grids:
            continue
        d = days.index(pl["day"])
        grids[f][d][pl["period"]] = f'{pl["course"]} {pl["section"]}'
        load[f] += 1
    for fid in inst.faculty:
        out[fid] = {"name": inst.faculty[fid].name,
                    "grid": grids[fid],
                    "weekly_load": load[fid]}
    return out


def room_utilisation(inst: Institute, placements: List[dict]) -> Dict[str, dict]:
    days = inst.calendar.working_days
    P = inst.calendar.periods_per_day
    total = len(days) * P
    used = defaultdict(int)
    grids = {rid: _empty_grid(days, P) for rid in inst.rooms}
    for pl in placements:
        r = pl["room"]
        if r not in grids:
            continue
        d = days.index(pl["day"])
        grids[r][d][pl["period"]] = f'{pl["course"]} {pl["section"]}'
        used[r] += 1
    return {rid: {"grid": grids[rid],
                  "used": used[rid],
                  "utilisation_pct": round(100 * used[rid] / total, 1)}
            for rid in inst.rooms}


def master_grid(inst: Institute, placements: List[dict]) -> List[dict]:
    """Flat administrative master list sorted by day/period."""
    days = inst.calendar.working_days
    rows = sorted(placements,
                  key=lambda p: (days.index(p["day"]), p["period"],
                                 p["section"]))
    return rows


def validate_placements(inst: Institute, placements: List[dict]) -> dict:
    """Independent re-check that ZERO hard constraints are violated.

    This is intentionally separate from the solver so the output can be
    trusted even if the model had a bug (defence in depth)."""
    seen_fac = set()
    seen_room = set()
    seen_sec = set()
    violations: List[str] = []
    for pl in placements:
        key = (pl["day"], pl["period"])
        fk = (key, pl["faculty"])
        rk = (key, pl["room"])
        sk = (key, pl["section"])
        if pl["faculty"] != "__none__" and fk in seen_fac:
            violations.append(f"Faculty clash: {pl['faculty']} at {key}")
        if rk in seen_room:
            violations.append(f"Room clash: {pl['room']} at {key}")
        if sk in seen_sec:
            violations.append(f"Section clash: {pl['section']} at {key}")
        seen_fac.add(fk)
        seen_room.add(rk)
        seen_sec.add(sk)

    # weekly quota check
    placed = defaultdict(int)
    for pl in placements:
        placed[(pl["section"], pl["course"], pl["kind"])] += 1
    for c in inst.courses.values():
        got_l = placed[(c.section_id, c.code, "L")]
        if got_l != c.lectures_per_week:
            violations.append(
                f"Quota: {c.code}/{c.section_id} lectures {got_l} "
                f"!= {c.lectures_per_week}")

    return {"hard_violations": len(violations),
            "details": violations,
            "valid": len(violations) == 0}


# ----------------------------------------------------------------- exporters
def to_json(obj) -> str:
    return json.dumps(obj, indent=2, default=str)


def section_grid_to_csv(inst: Institute, sid: str, grid: list) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    header = ["Day \\ Period"] + [f"P{p+1}"
                                  for p in range(inst.calendar.periods_per_day)]
    w.writerow(header)
    for di, day in enumerate(inst.calendar.working_days):
        w.writerow([day] + [cell.replace("\n", " | ") for cell in grid[di]])
    return buf.getvalue()


def to_ics(inst: Institute, placements: List[dict], who_key: str = "section",
           who: str = "") -> str:
    """Minimal ICS export (one recurring-free week) for a section or faculty."""
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//ttgen//EN"]
    for i, pl in enumerate(placements):
        if who and pl.get(who_key) != who:
            continue
        lines += [
            "BEGIN:VEVENT",
            f"UID:ttgen-{i}@local",
            f"SUMMARY:{pl['course']} ({pl['kind']}) {pl['section']}",
            f"LOCATION:{pl['room']}",
            f"DESCRIPTION:Faculty {pl['faculty']}; {pl['day']} period {pl['period']+1}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\n".join(lines)
