"""
streamlit_app.py
================
Streamlit front-end for the B.Tech CP-SAT timetable generator.

This UI reuses the existing core (models / solver / diagnostics / outputs)
without changing any solver internals. It solves the bundled sample instance
and lets you browse per-section, per-faculty and per-room timetables, see the
independent validation report, and download JSON / CSV / ICS exports.

Deployed on Streamlit Community Cloud:
    Main file path: streamlit_app.py
"""
from __future__ import annotations

import streamlit as st

import outputs
from diagnostics import explain_infeasible, pre_solve_report
from sample_data import build_sample
from solver import TimetableSolver

DEFAULT_WEIGHTS = {
    "gap_minimization": 5,
    "even_spread": 4,
    "morning_for_core": 3,
    "faculty_balance": 4,
    "room_locality": 2,
    "faculty_preference": 2,
    "schedule_stability_on_resolve": 6,
}

st.set_page_config(
    page_title="B.Tech Timetable Generator",
    page_icon="📅",
    layout="wide",
)


def grid_to_rows(inst, grid):
    """Turn a [day][period] grid into a list-of-dict table for st.dataframe."""
    periods = inst.calendar.periods_per_day
    days = inst.calendar.working_days
    rows = []
    for di, day in enumerate(days):
        row = {"Day": day}
        for p in range(periods):
            row[f"P{p + 1}"] = grid[di][p].replace("\n", " | ")
        rows.append(row)
    return rows


@st.cache_data(show_spinner=False)
def solve(time_limit_s: int, seed: int):
    """Solve the bundled sample. Cached so reruns are instant for same args."""
    inst = build_sample()
    data_errors = inst.validate()
    issues = pre_solve_report(inst)
    solver = TimetableSolver(
        inst, DEFAULT_WEIGHTS, time_limit_s=time_limit_s, seed=seed
    )
    result = solver.solve()
    payload = {
        "status": result.status,
        "objective": getattr(result, "objective", None),
        "soft_penalty": getattr(result, "soft_penalty", None),
        "placements": result.placements
        if result.status in ("OPTIMAL", "FEASIBLE")
        else [],
        "data_errors": data_errors,
        "issues": issues,
    }
    if result.status not in ("OPTIMAL", "FEASIBLE"):
        payload["diagnosis"] = explain_infeasible(inst)
    return payload


st.title("📅 B.Tech Automated Timetable Generator")
st.caption(
    "Constraint-based (CP-SAT / OR-Tools) conflict-free timetabling. "
    "This demo solves the bundled sample institute."
)

with st.sidebar:
    st.header("Solver settings")
    time_limit_s = st.slider("Time budget (seconds)", 5, 300, 60, 5)
    seed = st.number_input("Random seed", min_value=0, value=1, step=1)
    run = st.button("Generate timetable", type="primary", use_container_width=True)

if "result" not in st.session_state:
    st.session_state.result = None

if run:
    with st.spinner("Solving the timetable…"):
        st.session_state.result = solve(int(time_limit_s), int(seed))

res = st.session_state.result
if res is None:
    st.info("Set a time budget in the sidebar and click **Generate timetable**.")
    st.stop()

if res["data_errors"]:
    st.error("Data errors detected:")
    for e in res["data_errors"]:
        st.write("- ", e)

if res["issues"]:
    with st.expander("Pre-solve bottlenecks detected", expanded=False):
        for it in res["issues"]:
            st.write(f"**[{it['kind']}] {it['resource']}** — {it['suggestion']}")

status = res["status"]
if status not in ("OPTIMAL", "FEASIBLE"):
    st.error(f"Solver status: {status} — no feasible timetable.")
    st.subheader("Infeasibility diagnosis")
    st.code(outputs.to_json(res.get("diagnosis", {})), language="json")
    st.stop()

inst = build_sample()
placements = res["placements"]
report = outputs.validate_placements(inst, placements)

c1, c2, c3 = st.columns(3)
c1.metric("Solver status", status)
c2.metric("Hard violations", report["hard_violations"])
c3.metric("Soft penalty", res.get("soft_penalty"))

if report["valid"]:
    st.success("Independent validation passed: zero hard-constraint violations.")
else:
    st.warning("Validation found issues:")
    for d in report.get("details", []):
        st.write("- ", d)

sec_grids = outputs.section_grids(inst, placements)
fac_grids = outputs.faculty_grids(inst, placements)
room_util = outputs.room_utilisation(inst, placements)

tab_sec, tab_fac, tab_room, tab_export = st.tabs(
    ["Sections", "Faculty", "Rooms", "Export"]
)

with tab_sec:
    sid = st.selectbox("Section", sorted(sec_grids.keys()))
    st.dataframe(
        grid_to_rows(inst, sec_grids[sid]),
        use_container_width=True,
        hide_index=True,
    )

with tab_fac:
    fid = st.selectbox(
        "Faculty",
        sorted(fac_grids.keys()),
        format_func=lambda f: f"{f} — {fac_grids[f]['name']}",
    )
    st.caption(f"Weekly load: {fac_grids[fid]['weekly_load']} periods")
    st.dataframe(
        grid_to_rows(inst, fac_grids[fid]["grid"]),
        use_container_width=True,
        hide_index=True,
    )

with tab_room:
    rid = st.selectbox(
        "Room",
        sorted(room_util.keys()),
        format_func=lambda r: f"{r} — {room_util[r]['utilisation_pct']}% used",
    )
    st.caption(
        f"Used {room_util[rid]['used']} periods "
        f"({room_util[rid]['utilisation_pct']}% utilisation)"
    )
    st.dataframe(
        grid_to_rows(inst, room_util[rid]["grid"]),
        use_container_width=True,
        hide_index=True,
    )

with tab_export:
    st.write("Download the generated timetable in various formats.")
    full = {
        "status": status,
        "objective": res.get("objective"),
        "validation": report,
        "placements": placements,
        "faculty": fac_grids,
        "rooms": room_util,
    }
    st.download_button(
        "Download full result (JSON)",
        data=outputs.to_json(full),
        file_name="timetable.json",
        mime="application/json",
    )
    exp_sid = st.selectbox("Section for CSV / ICS export", sorted(sec_grids.keys()))
    st.download_button(
        "Download section grid (CSV)",
        data=outputs.section_grid_to_csv(inst, exp_sid, sec_grids[exp_sid]),
        file_name=f"timetable_{exp_sid}.csv",
        mime="text/csv",
    )
    st.download_button(
        "Download section calendar (ICS)",
        data=outputs.to_ics(inst, placements, who_key="section", who=exp_sid),
        file_name=f"timetable_{exp_sid}.ics",
        mime="text/calendar",
    )
