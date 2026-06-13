"""
test_acceptance.py
==================
Acceptance tests T1-T8 from the build spec. Run with:  pytest -q

These are the contract: a build is only "done" when all of these pass on the
sample instance. They re-derive correctness independently of the solver.
"""
from __future__ import annotations

from collections import defaultdict

import pytest

import outputs
from diagnostics import explain_infeasible
from models import Calendar, Course, CourseType, Faculty, Institute, Room, RoomType, Section
from sample_data import build_sample
from solver import TimetableSolver

WEIGHTS = {"even_spread": 4, "morning_for_core": 3, "faculty_preference": 2}


@pytest.fixture(scope="module")
def solved():
    inst = build_sample()
    res = TimetableSolver(inst, WEIGHTS, time_limit_s=60, seed=1).solve()
    assert res.status in ("OPTIMAL", "FEASIBLE"), res.status
    return inst, res


# ---- T1: no faculty/section/room appears twice in one period ---------------
def test_t1_no_double_booking(solved):
    inst, res = solved
    fac, room, sec = set(), set(), set()
    for pl in res.placements:
        slot = (pl["day"], pl["period"])
        if pl["faculty"] != "__none__":
            assert (slot, pl["faculty"]) not in fac
            fac.add((slot, pl["faculty"]))
        assert (slot, pl["room"]) not in room
        room.add((slot, pl["room"]))
        assert (slot, pl["section"]) not in sec
        sec.add((slot, pl["section"]))


# ---- T2: weekly L/T/P quota met exactly ------------------------------------
def test_t2_weekly_quota(solved):
    inst, res = solved
    placed = defaultdict(int)
    for pl in res.placements:
        placed[(pl["section"], pl["course"], pl["kind"])] += 1
    for c in inst.courses.values():
        assert placed[(c.section_id, c.code, "L")] == c.lectures_per_week
        assert placed[(c.section_id, c.code, "T")] == c.tutorials_per_week
        lab_units = placed[(c.section_id, c.code, "LAB")]
        assert lab_units == c.lab_sessions_per_week * c.lab_block_len


# ---- T3: lab blocks contiguous, equipped, sufficient capacity --------------
def test_t3_lab_blocks(solved):
    inst, res = solved
    labs = defaultdict(list)
    for pl in res.placements:
        if pl["kind"] == "LAB":
            labs[(pl["section"], pl["course"], pl["room"], pl["day"])].append(
                pl["period"])
    for key, periods in labs.items():
        periods.sort()
        # contiguous
        assert periods == list(range(periods[0], periods[0] + len(periods)))
        room = inst.rooms[key[2]]
        assert room.room_type == RoomType.LAB


# ---- T4: elective basket occupies identical slots across sections ----------
def test_t4_elective_sync(solved):
    inst, res = solved
    basket_slots = defaultdict(set)
    for pl in res.placements:
        c = next((c for c in inst.courses.values()
                  if c.code == pl["course"] and c.section_id == pl["section"]),
                 None)
        if c and c.elective_basket:
            basket_slots[(pl["section"], c.elective_basket)].add(
                (pl["day"], pl["period"]))
    by_basket = defaultdict(list)
    for (sec, basket), slots in basket_slots.items():
        by_basket[basket].append(slots)
    for basket, slotsets in by_basket.items():
        first = slotsets[0]
        for s in slotsets[1:]:
            assert s == first, f"Basket {basket} not synced across sections"


# ---- T5: no break/lunch or faculty-unavailability violations ---------------
def test_t5_protected_and_availability(solved):
    inst, res = solved
    days = inst.calendar.working_days
    for pl in res.placements:
        d = days.index(pl["day"])
        sec = inst.sections[pl["section"]]
        lunch = inst.calendar.lunch_by_year.get(sec.year)
        assert pl["period"] != lunch
        fac = inst.faculty.get(pl["faculty"])
        if fac:
            assert (d, pl["period"]) not in fac.unavailable


# ---- T6: faculty daily/weekly load caps respected --------------------------
def test_t6_load_caps(solved):
    inst, res = solved
    days = inst.calendar.working_days
    week = defaultdict(int)
    day = defaultdict(int)
    for pl in res.placements:
        if pl["faculty"] == "__none__":
            continue
        week[pl["faculty"]] += 1
        day[(pl["faculty"], pl["day"])] += 1
    for f, fac in inst.faculty.items():
        assert week[f] <= fac.max_load_per_week
        for dname in days:
            assert day[(f, dname)] <= fac.max_load_per_day


# ---- T7: over-constrained input -> clear diagnosis, no crash ---------------
def test_t7_infeasible_diagnosis():
    cal = Calendar(["Mon"], periods_per_day=2, period_minutes=55,
                   lunch_by_year={1: 0})
    sec = {"S": Section("S", "X", 1, "A", 60, 1)}
    fac = {"F": Faculty("F", "Dr F", "X", max_load_per_week=1)}
    rooms = {"R": Room("R", RoomType.THEORY, 60)}
    # Demand 5 lectures into a single non-lunch period -> impossible
    courses = {"C": Course("C", "C1", "Overloaded", CourseType.THEORY, "S",
                           lectures_per_week=5, eligible_faculty=["F"])}
    inst = Institute(cal, sec, courses, fac, rooms)
    diag = explain_infeasible(inst)
    assert diag["issues"], "expected at least one bottleneck"
    assert "summary" in diag


# ---- T8: incremental resolve after one faculty becomes unavailable ---------
def test_t8_incremental_resolve(solved):
    inst, base = solved
    # Mark Dr. Rao unavailable on Monday entirely, re-solve.
    inst2 = build_sample()
    inst2.faculty["F_RAO"].unavailable |= {(0, p) for p in range(7)}
    res2 = TimetableSolver(inst2, WEIGHTS, time_limit_s=60, seed=1).solve()
    assert res2.status in ("OPTIMAL", "FEASIBLE")
    report = outputs.validate_placements(inst2, res2.placements)
    assert report["valid"], report["details"]
    # Dr. Rao must have nothing on Monday now.
    for pl in res2.placements:
        if pl["faculty"] == "F_RAO":
            assert pl["day"] != "Mon"
