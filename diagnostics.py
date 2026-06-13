"""
diagnostics.py
==============
Infeasibility analysis. When the solver cannot place a timetable, this module
explains *why* in human terms instead of just returning "INFEASIBLE".

It does cheap, deterministic capacity accounting BEFORE the solver runs and
also interprets a failed solve afterwards, so an administrator gets concrete,
actionable relaxations.
"""
from __future__ import annotations

from typing import Dict, List

from models import Course, Institute, RoomType


def _room_hours_by_type(inst: Institute) -> Dict[RoomType, int]:
    slots = inst.calendar.num_days * inst.calendar.periods_per_day
    out: Dict[RoomType, int] = {}
    for r in inst.rooms.values():
        out[r.room_type] = out.get(r.room_type, 0) + slots
    return out


def _demand_hours_by_type(inst: Institute) -> Dict[RoomType, int]:
    out: Dict[RoomType, int] = {}
    for c in inst.courses.values():
        hrs = c.lectures_per_week + c.tutorials_per_week \
              + c.lab_sessions_per_week * c.lab_block_len
        out[c.required_room_type] = out.get(c.required_room_type, 0) + hrs
    return out


def pre_solve_report(inst: Institute) -> List[dict]:
    """Cheap feasibility checks producing a list of issues (may be empty)."""
    issues: List[dict] = []
    slots_per_room = inst.calendar.num_days * inst.calendar.periods_per_day

    # 1) room/lab capacity in HOURS vs demand, per room type
    supply = _room_hours_by_type(inst)
    demand = _demand_hours_by_type(inst)
    for rt, need in demand.items():
        have = supply.get(rt, 0)
        if need > have:
            extra_rooms = -(-(need - have) // slots_per_room)
            issues.append({
                "kind": "ROOM_CAPACITY",
                "resource": rt.value,
                "needed_hours": need,
                "available_hours": have,
                "suggestion": f"Add {extra_rooms} more {rt.value} room(s), "
                              f"or reduce {rt.value} load.",
            })

    # 2) faculty weekly load over capacity (worst-case first-eligible signal)
    fac_demand: Dict[str, int] = {}
    for c in inst.courses.values():
        hrs = c.lectures_per_week + c.tutorials_per_week \
              + c.lab_sessions_per_week * c.lab_block_len
        if c.eligible_faculty:
            f = c.eligible_faculty[0]
            fac_demand[f] = fac_demand.get(f, 0) + hrs
    for f, load in fac_demand.items():
        cap = inst.faculty[f].max_load_per_week
        if load > cap:
            issues.append({
                "kind": "FACULTY_OVERLOAD",
                "resource": f,
                "needed_hours": load,
                "available_hours": cap,
                "suggestion": f"Spread {inst.faculty[f].name}'s courses across "
                              f"more faculty, or raise max_load_per_week ({cap}).",
            })

    # 3) lab block cannot fit in the day grid
    P = inst.calendar.periods_per_day
    for c in inst.courses.values():
        if c.lab_sessions_per_week and c.lab_block_len > P:
            issues.append({
                "kind": "LAB_BLOCK_TOO_LONG",
                "resource": c.code,
                "needed_hours": c.lab_block_len,
                "available_hours": P,
                "suggestion": f"{c.code}: lab block of {c.lab_block_len} periods "
                              f"exceeds {P} periods/day. Shorten the block.",
            })

    # 4) equipment mismatch: no eligible room exists
    for c in inst.courses.values():
        if c.lab_sessions_per_week or c.required_room_type != RoomType.THEORY:
            ok = any(r.room_type == c.required_room_type
                     and c.required_equipment.issubset(r.equipment)
                     for r in inst.rooms.values())
            if not ok:
                issues.append({
                    "kind": "NO_EQUIPPED_ROOM",
                    "resource": c.code,
                    "needed_hours": 0,
                    "available_hours": 0,
                    "suggestion": f"No {c.required_room_type.value} room has "
                                  f"equipment {sorted(c.required_equipment)}.",
                })

    # 5) elective basket consistency: members must share weekly footprint
    baskets: Dict[str, List[Course]] = {}
    for c in inst.courses.values():
        if c.elective_basket:
            baskets.setdefault(c.elective_basket, []).append(c)
    for bid, members in baskets.items():
        foot = {m.lectures_per_week for m in members}
        if len(foot) > 1:
            issues.append({
                "kind": "ELECTIVE_FOOTPRINT_MISMATCH",
                "resource": bid,
                "needed_hours": 0,
                "available_hours": 0,
                "suggestion": f"Basket '{bid}' members have differing lecture "
                              f"counts {foot}; they must match to share a slot.",
            })

    return issues


def explain_infeasible(inst: Institute) -> dict:
    """Bundle a full diagnosis suitable for returning to the UI/API."""
    issues = pre_solve_report(inst)
    if issues:
        summary = (f"{len(issues)} bottleneck(s) detected. "
                   f"See 'issues' for concrete relaxations.")
    else:
        summary = ("No obvious capacity bottleneck found; infeasibility is "
                   "likely due to interacting clashes - try relaxing "
                   "one_subject_per_day_theory to soft, or freeing a busy "
                   "faculty slot.")
    return {
        "feasible_prima_facie": len(issues) == 0,
        "issues": issues,
        "summary": summary,
    }
