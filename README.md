# B.Tech Automated Timetable Generator

A production-oriented, constraint-based timetable generator for engineering
(B.Tech) colleges. It models timetabling as what it actually is — a
**constraint-satisfaction + optimization problem (CSP/COP)**, NP-hard in
general — and solves the whole interlocking schedule (every section, faculty,
and room) at once using Google OR-Tools **CP-SAT**.

When no valid timetable exists, it does **not** fail silently: it returns a
human-readable diagnosis of the bottleneck and concrete relaxations.

---

## Why this exists

A B.Tech timetable is dozens of interlocking schedules that must all be
mutually consistent: multiple branches x years x sections, lab batches,
shared professors and labs, and electives that dissolve section boundaries
into globally-aligned "baskets". Fixing one clash creates another, so the
solver must reason over the whole graph simultaneously.

---

## Architecture (clean separation)

| Layer | File | Responsibility |
|-------|------|----------------|
| Data model | `models.py` | Entities (Calendar, Section, Course, Faculty, Room, Institute) + validation. No solver logic. |
| Constraints + solver | `solver.py` | CP-SAT model: hard rules as infeasibility, soft rules as weighted objective penalties. |
| Diagnostics | `diagnostics.py` | Pre-solve capacity accounting + infeasibility explanation. |
| Presentation | `outputs.py` | Section / faculty / room grids, independent validation, JSON / CSV / ICS export. |
| Sample data | `sample_data.py` | A realistic small instance exercising every constraint family. |
| Runner | `main.py` | CLI: validate -> diagnose -> solve -> validate -> print/export. |
| Config | `config.yaml` | Data-driven weights, periods, breaks. |
| Tests | `test_acceptance.py` | Acceptance tests T1-T8. |

New constraints are added in `solver.py` (a new `_add_*` method) **without
touching solver internals** or any other layer.

---

## Hard constraints (never violated)

Faculty / section / room clash-freedom; lab-batch exclusivity; capacity;
equipment match; exact weekly L/T/P quota; lab continuity (contiguous block,
never crossing a break); faculty availability; daily & weekly load caps;
protected (staggered) breaks/lunch; **elective-basket synchronization** across
all participating sections; one-subject-per-day for theory (configurable); and
immovable locked pre-placements.

## Soft constraints (minimized as weighted penalty)

Even spread of a course across days, morning preference for core theory,
faculty preferred slots, faculty load balance, gap minimization, room
locality, and schedule **stability** on incremental re-solve.

---

## Quick start

```bash
pip install -r requirements.txt

# Solve the bundled sample and print section grids
python main.py

# 60-second budget, only show one section
python main.py --time 60 --section CSE-2A

# Full machine-readable result (placements + faculty + room utilisation)
python main.py --json

# Run the acceptance tests
pytest -q
```

---

## How to add a new constraint

1. Open `solver.py`.
2. Add a private method, e.g. `_add_no_friday_labs(self)`, building CP
   constraints (hard) or appending to the penalty list (soft).
3. Call it from `_add_hard_constraints` or `_add_soft_objective`.
4. No other file changes — the data model and presentation are untouched.

## How to tune weights

Edit `soft_weights` in `config.yaml` (or pass `--config`). Higher weight =
stronger preference. Hard rules are never weighted.

## Incremental re-solve (schedule stability)

Mark a faculty unavailable (set `Faculty.unavailable`) or a room out of
service, then re-run. The `schedule_stability_on_resolve` weight is intended
to penalize moving classes that did not need to move, so the new timetable
stays close to the old one. Test `T8` exercises this path.

---

## Acceptance tests (T1-T8)

```
T1  No faculty/section/room double-booked in any period.
T2  Every course's L/T/P weekly quota met exactly.
T3  Every lab block contiguous, in an equipped lab of sufficient capacity.
T4  Every elective basket occupies identical slots across all sections.
T5  No session violates a protected break/lunch or faculty unavailability.
T6  No faculty exceeds daily/weekly load caps.
T7  Over-constrained input returns a clear infeasibility diagnosis (no crash).
T8  After a faculty becomes unavailable, an incremental re-solve stays valid.
```

Run them with `pytest -q`.

---

## Edge cases handled

Shared faculty across sections/branches/years; parallel lab batches under
different instructors; college-wide open-elective common slots; visiting
faculty available only on specific days (see `F_GUEST` in the sample);
staggered lunch per year; equipment/software-specific labs; locked
NPTEL/MOOC/national slots; and infeasibility diagnosis with suggested
relaxations.

---

## Roadmap (not yet implemented here)

A FastAPI/Django REST wrapper, PostgreSQL persistence, a React drag-and-drop
override editor that re-validates live, PDF/Excel exporters, and metaheuristic
seeding (graph-coloring -> CP-SAT) for very large instances. The current core
is intentionally dependency-light and runnable from the command line.

---

## License

Add a license of your choice (e.g. MIT) before public/production use.
