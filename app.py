"""
app.py
======
FastAPI web service wrapping the CP-SAT timetable solver.

Endpoints
---------
GET  /                       -> small HTML landing page + links
GET  /health                 -> liveness probe (for the host)
GET  /api/sample             -> the bundled sample Institute as JSON
POST /api/solve              -> solve an Institute, return placements + views
POST /api/diagnose           -> pre-solve bottleneck diagnosis only
GET  /api/solve-sample       -> convenience: solve the bundled sample
GET  /api/timetable/section/{sid}  -> one section grid (from last sample solve)

The service keeps the heavy solver out of the request thread budget by using a
configurable time limit. For a real deployment, put long solves on a task
queue; this synchronous version is fine for the small demo instance.
"""
from __future__ import annotations

import os
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

import outputs
from diagnostics import explain_infeasible, pre_solve_report
from models import (Calendar, Course, CourseType, Faculty, Institute, Room,
                    RoomType, Section)
from sample_data import build_sample
from solver import TimetableSolver

app = FastAPI(
    title="B.Tech Timetable Generator",
    description="CP-SAT based conflict-free timetable generation service.",
    version="1.0.0",
)

DEFAULT_WEIGHTS = {
    "even_spread": 4,
    "morning_for_core": 3,
    "faculty_preference": 2,
}


# --------------------------------------------------------------------- schema
class SolveOptions(BaseModel):
    time_limit_s: int = Field(60, ge=1, le=600)
    seed: int = 1
    weights: Optional[Dict[str, int]] = None


# ------------------------------------------------------------ (de)serialisers
def institute_to_dict(inst: Institute) -> dict:
    return {
        "calendar": {
            "working_days": inst.calendar.working_days,
            "periods_per_day": inst.calendar.periods_per_day,
            "period_minutes": inst.calendar.period_minutes,
            "short_break_after_period": inst.calendar.short_break_after_period,
            "lunch_by_year": inst.calendar.lunch_by_year,
        },
        "sections": {sid: vars(s) for sid, s in inst.sections.items()},
        "faculty": {fid: {
            "id": f.id, "name": f.name, "department": f.department,
            "unavailable": [list(t) for t in f.unavailable],
            "max_load_per_day": f.max_load_per_day,
            "max_load_per_week": f.max_load_per_week,
            "preferred": [list(t) for t in f.preferred],
        } for fid, f in inst.faculty.items()},
        "rooms": {rid: {
            "id": r.id, "room_type": r.room_type.value, "capacity": r.capacity,
            "equipment": sorted(r.equipment), "owner_dept": r.owner_dept,
            "shared": r.shared,
        } for rid, r in inst.rooms.items()},
        "courses": {cid: {
            "id": c.id, "code": c.code, "title": c.title,
            "course_type": c.course_type.value, "section_id": c.section_id,
            "lectures_per_week": c.lectures_per_week,
            "tutorials_per_week": c.tutorials_per_week,
            "lab_sessions_per_week": c.lab_sessions_per_week,
            "lab_block_len": c.lab_block_len, "credits": c.credits,
            "eligible_faculty": c.eligible_faculty,
            "required_room_type": c.required_room_type.value,
            "required_equipment": sorted(c.required_equipment),
            "elective_basket": c.elective_basket,
            "locked_slots": [list(t) for t in c.locked_slots],
        } for cid, c in inst.courses.items()},
        "one_subject_per_day_theory": inst.one_subject_per_day_theory,
    }


def institute_from_dict(d: dict) -> Institute:
    cal = Calendar(
        working_days=d["calendar"]["working_days"],
        periods_per_day=d["calendar"]["periods_per_day"],
        period_minutes=d["calendar"].get("period_minutes", 55),
        short_break_after_period=d["calendar"].get("short_break_after_period"),
        lunch_by_year={int(k): v for k, v in
                       d["calendar"].get("lunch_by_year", {}).items()},
    )
    sections = {sid: Section(**s) for sid, s in d["sections"].items()}
    faculty = {}
    for fid, f in d["faculty"].items():
        faculty[fid] = Faculty(
            id=f["id"], name=f["name"], department=f["department"],
            unavailable={tuple(t) for t in f.get("unavailable", [])},
            max_load_per_day=f.get("max_load_per_day", 6),
            max_load_per_week=f.get("max_load_per_week", 24),
            preferred={tuple(t) for t in f.get("preferred", [])})
    rooms = {}
    for rid, r in d["rooms"].items():
        rooms[rid] = Room(
            id=r["id"], room_type=RoomType(r["room_type"]),
            capacity=r["capacity"], equipment=set(r.get("equipment", [])),
            owner_dept=r.get("owner_dept"), shared=r.get("shared", True))
    courses = {}
    for cid, c in d["courses"].items():
        courses[cid] = Course(
            id=c["id"], code=c["code"], title=c["title"],
            course_type=CourseType(c["course_type"]),
            section_id=c["section_id"],
            lectures_per_week=c.get("lectures_per_week", 0),
            tutorials_per_week=c.get("tutorials_per_week", 0),
            lab_sessions_per_week=c.get("lab_sessions_per_week", 0),
            lab_block_len=c.get("lab_block_len", 2),
            credits=c.get("credits", 0),
            eligible_faculty=c.get("eligible_faculty", []),
            required_room_type=RoomType(c.get("required_room_type", "THEORY")),
            required_equipment=set(c.get("required_equipment", [])),
            elective_basket=c.get("elective_basket"),
            locked_slots=[tuple(t) for t in c.get("locked_slots", [])])
    return Institute(cal, sections, courses, faculty, rooms,
                     d.get("one_subject_per_day_theory", True))


def run_solve(inst: Institute, opts: SolveOptions) -> dict:
    errors = inst.validate()
    if errors:
        raise HTTPException(status_code=422,
                            detail={"data_errors": errors})
    weights = opts.weights or DEFAULT_WEIGHTS
    solver = TimetableSolver(inst, weights, time_limit_s=opts.time_limit_s,
                             seed=opts.seed)
    result = solver.solve()
    if result.status not in ("OPTIMAL", "FEASIBLE"):
        return {"status": result.status,
                "diagnosis": explain_infeasible(inst)}
    report = outputs.validate_placements(inst, result.placements)
    return {
        "status": result.status,
        "objective": result.objective,
        "validation": report,
        "placements": result.placements,
        "section_grids": outputs.section_grids(inst, result.placements),
        "faculty": outputs.faculty_grids(inst, result.placements),
        "rooms": outputs.room_utilisation(inst, result.placements),
    }


# ----------------------------------------------------------------- endpoints
@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return """
    <html><head><title>B.Tech Timetable Generator</title>
    <style>body{font-family:system-ui;max-width:760px;margin:40px auto;padding:0 16px;color:#222}
    code{background:#f3f3f3;padding:2px 6px;border-radius:4px}
    a{color:#0969da}</style></head>
    <body>
      <h1>B.Tech Timetable Generator</h1>
      <p>CP-SAT based, conflict-free timetable generation as a web service.</p>
      <ul>
        <li><a href="/docs">Interactive API docs (Swagger)</a></li>
        <li><a href="/api/solve-sample">Solve the bundled sample &rarr; JSON</a></li>
        <li><a href="/api/sample">Inspect the sample input &rarr; JSON</a></li>
        <li><a href="/health">Health check</a></li>
      </ul>
      <p>POST your own institute JSON to <code>/api/solve</code>. See
      <code>/docs</code> for the schema.</p>
    </body></html>
    """


@app.get("/api/sample")
def get_sample() -> dict:
    return institute_to_dict(build_sample())


@app.get("/api/solve-sample")
def solve_sample(time_limit_s: int = 60, seed: int = 1) -> JSONResponse:
    inst = build_sample()
    res = run_solve(inst, SolveOptions(time_limit_s=time_limit_s, seed=seed))
    return JSONResponse(res)


@app.post("/api/solve")
def solve(institute: dict, options: SolveOptions = SolveOptions()) -> dict:
    inst = institute_from_dict(institute)
    return run_solve(inst, options)


@app.post("/api/diagnose")
def diagnose(institute: dict) -> dict:
    inst = institute_from_dict(institute)
    return {"issues": pre_solve_report(inst),
            "diagnosis": explain_infeasible(inst)}


@app.get("/api/timetable/section/{sid}")
def section_view(sid: str, time_limit_s: int = 60) -> dict:
    inst = build_sample()
    if sid not in inst.sections:
        raise HTTPException(status_code=404, detail=f"Unknown section '{sid}'")
    res = run_solve(inst, SolveOptions(time_limit_s=time_limit_s))
    grids = res.get("section_grids", {})
    return {"section": sid, "grid": grids.get(sid)}


if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run("app:app", host="0.0.0.0", port=port)
