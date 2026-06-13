"""
solver.py
=========
CP-SAT based timetable solver.

Design:
  * Every (course, occurrence) needs to be placed into a (day, period, room,
    faculty) tuple. We create boolean "assignment" variables and add hard
    constraints so that no infeasible combination can ever be selected.
  * Hard constraints  -> modelled as CP-SAT constraints (infeasibility).
  * Soft constraints  -> modelled as penalty terms added to the objective,
    which the solver MINIMIZES. Weights come from config.

We deliberately keep the modelling readable rather than maximally compact so
new constraints can be added without touching solver internals (see README).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from ortools.sat.python import cp_model

from models import Course, CourseType, Institute, RoomType


# A single atomic teaching unit that must occupy ONE period in ONE room with
# ONE faculty. Lectures/tutorials produce one Meeting each; a lab session of
# block length L produces L consecutive Meetings tied together.
@dataclass(frozen=True)
class Meeting:
    course_id: str
    section_id: str
    kind: str                 # "L", "T", or "LAB"
    occurrence: int           # which lecture/tutorial/lab of the week
    block_pos: int            # position within a lab block (0 for L/T)
    block_len: int            # 1 for L/T, lab_block_len for labs


@dataclass
class SolveResult:
    status: str
    placements: List[dict]
    soft_penalty: int
    objective: int
    diagnostics: Optional[dict] = None


class TimetableSolver:
    def __init__(self, inst: Institute, weights: Dict[str, int],
                 time_limit_s: int = 120, seed: int = 1):
        self.inst = inst
        self.w = weights
        self.time_limit_s = time_limit_s
        self.seed = seed
        self.model = cp_model.CpModel()
        self.cal = inst.calendar

        self.meetings: List[Meeting] = []
        # x[(m_idx, d, p, r, f)] = 1  -> meeting placed there
        self.x: Dict[Tuple[int, int, int, str, str], cp_model.IntVar] = {}
        self._build_meetings()

    # ------------------------------------------------------------------ build
    def _build_meetings(self) -> None:
        for c in self.inst.courses.values():
            for i in range(c.lectures_per_week):
                self.meetings.append(Meeting(c.id, c.section_id, "L", i, 0, 1))
            for i in range(c.tutorials_per_week):
                self.meetings.append(Meeting(c.id, c.section_id, "T", i, 0, 1))
            for i in range(c.lab_sessions_per_week):
                for b in range(c.lab_block_len):
                    self.meetings.append(
                        Meeting(c.id, c.section_id, "LAB", i, b, c.lab_block_len))

    def _eligible_rooms(self, c: Course) -> List[str]:
        rooms = []
        for r in self.inst.rooms.values():
            if r.room_type != c.required_room_type:
                continue
            if not c.required_equipment.issubset(r.equipment):
                continue
            rooms.append(r.id)
        return rooms

    def _slot_protected(self, year: int, period: int) -> bool:
        lunch = self.cal.lunch_by_year.get(year)
        return lunch is not None and period == lunch

    # ------------------------------------------------------------------ vars
    def build(self) -> None:
        m = self.model
        for mi, mt in enumerate(self.meetings):
            c = self.inst.courses[mt.course_id]
            sec = self.inst.sections[mt.section_id]
            rooms = self._eligible_rooms(c)
            facs = c.eligible_faculty or ["__none__"]
            choices = []
            for d in range(self.cal.num_days):
                for p in range(self.cal.periods_per_day):
                    if self._slot_protected(sec.year, p):
                        continue
                    # lab block must fit before end of day and not cross lunch
                    if mt.kind == "LAB":
                        start = p - mt.block_pos
                        if start < 0 or start + mt.block_len > self.cal.periods_per_day:
                            continue
                        crosses = any(self._slot_protected(sec.year, start + k)
                                      for k in range(mt.block_len))
                        if crosses:
                            continue
                    for r in rooms:
                        for f in facs:
                            # faculty availability (hard)
                            if (d, p) in self.inst.faculty.get(
                                    f, _Empty()).unavailable:
                                continue
                            v = m.NewBoolVar(f"x_{mi}_{d}_{p}_{r}_{f}")
                            self.x[(mi, d, p, r, f)] = v
                            choices.append(v)
            if not choices:
                # no legal placement at all -> record for diagnostics
                self._infeasible_meeting = mt
            # exactly one placement per meeting (weekly quota, hard)
            m.Add(sum(choices) == 1)

        self._add_hard_constraints()
        self._add_soft_objective()

    # ------------------------------------------------------------ hard rules
    def _add_hard_constraints(self) -> None:
        m = self.model
        cal = self.cal
        # group helpers
        by_slot_room: Dict[Tuple[int, int, str], list] = {}
        by_slot_fac: Dict[Tuple[int, int, str], list] = {}
        by_slot_section: Dict[Tuple[int, int, str], list] = {}
        for (mi, d, p, r, f), v in self.x.items():
            mt = self.meetings[mi]
            by_slot_room.setdefault((d, p, r), []).append(v)
            by_slot_fac.setdefault((d, p, f), []).append(v)
            by_slot_section.setdefault((d, p, mt.section_id), []).append(v)

        # (3) room clash: <= 1 meeting per room per slot
        for vs in by_slot_room.values():
            m.Add(sum(vs) <= 1)
        # (1) faculty clash
        for (d, p, f), vs in by_slot_fac.items():
            if f != "__none__":
                m.Add(sum(vs) <= 1)
        # (2) section clash (theory) AND (4) section vs its own lab block
        for vs in by_slot_section.values():
            m.Add(sum(vs) <= 1)

        # (10) faculty load caps
        for f, fac in self.inst.faculty.items():
            week_vars = [v for (mi, d, p, r, ff), v in self.x.items() if ff == f]
            if week_vars:
                m.Add(sum(week_vars) <= fac.max_load_per_week)
            for d in range(cal.num_days):
                day_vars = [v for (mi, dd, p, r, ff), v in self.x.items()
                            if ff == f and dd == d]
                if day_vars:
                    m.Add(sum(day_vars) <= fac.max_load_per_day)

        self._add_lab_continuity()
        self._add_elective_sync()
        self._add_one_subject_per_day()
        self._add_locks()

    def _add_lab_continuity(self) -> None:
        # All block_pos meetings of one lab occurrence share day & room & fac,
        # and occupy consecutive periods start..start+len-1.
        m = self.model
        labs: Dict[Tuple[str, int], List[int]] = {}
        for mi, mt in enumerate(self.meetings):
            if mt.kind == "LAB":
                labs.setdefault((mt.course_id, mt.occurrence), []).append(mi)
        for parts in labs.values():
            parts.sort(key=lambda i: self.meetings[i].block_pos)
            for a, b in zip(parts, parts[1:]):
                # link consecutive positions: if part a at (d,p,r,f) then part b
                # must be at (d, p+1, r, f).
                for (mi, d, p, r, f), va in self.x.items():
                    if mi != a:
                        continue
                    nb = self.x.get((b, d, p + 1, r, f))
                    if nb is None:
                        m.Add(va == 0)  # no valid continuation -> forbid
                    else:
                        m.Add(va <= nb)

    def _add_elective_sync(self) -> None:
        # Courses in the same basket must occupy identical (d,p) slots.
        m = self.model
        baskets: Dict[str, List[str]] = {}
        for c in self.inst.courses.values():
            if c.elective_basket:
                baskets.setdefault(c.elective_basket, []).append(c.id)
        for cids in baskets.values():
            slot_sets = []
            for cid in cids:
                mis = [i for i, mt in enumerate(self.meetings)
                       if mt.course_id == cid]
                slot_sets.append(mis)
            # For every slot, the "is used" indicator must match across courses.
            for d in range(self.cal.num_days):
                for p in range(self.cal.periods_per_day):
                    inds = []
                    for mis in slot_sets:
                        terms = [v for (mi, dd, pp, r, f), v in self.x.items()
                                 if mi in mis and dd == d and pp == p]
                        ind = m.NewBoolVar(f"bask_{d}_{p}_{len(inds)}")
                        if terms:
                            m.Add(sum(terms) == ind)
                        else:
                            m.Add(ind == 0)
                        inds.append(ind)
                    for a, b in zip(inds, inds[1:]):
                        m.Add(a == b)

    def _add_one_subject_per_day(self) -> None:
        if not self.inst.one_subject_per_day_theory:
            return
        m = self.model
        for c in self.inst.courses.values():
            if c.course_type not in (CourseType.THEORY, CourseType.ELECTIVE):
                continue
            mis = [i for i, mt in enumerate(self.meetings)
                   if mt.course_id == c.id and mt.kind == "L"]
            for d in range(self.cal.num_days):
                day_vars = [v for (mi, dd, p, r, f), v in self.x.items()
                            if mi in mis and dd == d]
                if day_vars:
                    m.Add(sum(day_vars) <= 1)

    def _add_locks(self) -> None:
        m = self.model
        for c in self.inst.courses.values():
            if not c.locked_slots:
                continue
            for (ld, lp) in c.locked_slots:
                terms = [v for (mi, d, p, r, f), v in self.x.items()
                         if self.meetings[mi].course_id == c.id
                         and d == ld and p == lp]
                if terms:
                    m.Add(sum(terms) >= 1)

    # ------------------------------------------------------------ soft rules
    def _add_soft_objective(self) -> None:
        m = self.model
        penalties = []

        # even spread: penalise two lectures of same course on same day
        for c in self.inst.courses.values():
            mis = [i for i, mt in enumerate(self.meetings)
                   if mt.course_id == c.id and mt.kind == "L"]
            for d in range(self.cal.num_days):
                day_vars = [v for (mi, dd, p, r, f), v in self.x.items()
                            if mi in mis and dd == d]
                if len(day_vars) > 1:
                    extra = m.NewIntVar(0, len(day_vars), f"spread_{c.id}_{d}")
                    m.Add(extra >= sum(day_vars) - 1)
                    penalties.append(self.w.get("even_spread", 4) * extra)

        # morning preference for core theory (later periods penalised)
        morning_cut = self.cal.periods_per_day // 2
        for (mi, d, p, r, f), v in self.x.items():
            mt = self.meetings[mi]
            c = self.inst.courses[mt.course_id]
            if c.course_type == CourseType.THEORY and p >= morning_cut:
                penalties.append(self.w.get("morning_for_core", 3) * v)

        # faculty preference: reward (negative penalty) preferred slots
        for (mi, d, p, r, f), v in self.x.items():
            fac = self.inst.faculty.get(f)
            if fac and (d, p) in fac.preferred:
                penalties.append(-self.w.get("faculty_preference", 2) * v)

        if penalties:
            m.Minimize(sum(penalties))

    # ------------------------------------------------------------------ solve
    def solve(self) -> SolveResult:
        self.build()
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = self.time_limit_s
        solver.parameters.random_seed = self.seed
        solver.parameters.num_search_workers = 8
        status = solver.Solve(self.model)
        status_name = solver.StatusName(status)

        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            placements = []
            for (mi, d, p, r, f), v in self.x.items():
                if solver.Value(v) == 1:
                    mt = self.meetings[mi]
                    c = self.inst.courses[mt.course_id]
                    placements.append({
                        "course": c.code, "title": c.title,
                        "section": mt.section_id, "kind": mt.kind,
                        "day": self.cal.working_days[d], "period": p,
                        "room": r, "faculty": f,
                    })
            return SolveResult(status_name, placements,
                               int(solver.ObjectiveValue()),
                               int(solver.ObjectiveValue()))
        return SolveResult(status_name, [], 0, 0)


class _Empty:
    unavailable: set = set()
