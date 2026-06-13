"""
main.py
=======
Command-line entry point. Loads the sample institute (or a JSON file),
runs pre-solve diagnostics, solves, validates, and prints/export the views.

Usage:
    python main.py                 # solve the bundled sample
    python main.py --time 60       # 60s time budget
    python main.py --section CSE-2A
"""
from __future__ import annotations

import argparse
import sys

import yaml

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


def load_weights(path: str | None) -> dict:
    if not path:
        return DEFAULT_WEIGHTS
    with open(path) as fh:
        cfg = yaml.safe_load(fh)
    return cfg.get("soft_weights", DEFAULT_WEIGHTS)


def print_section_grids(inst, placements, only=None):
    grids = outputs.section_grids(inst, placements)
    for sid, grid in grids.items():
        if only and sid != only:
            continue
        print(f"\n=== Section {sid} ===")
        print(outputs.section_grid_to_csv(inst, sid, grid))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="B.Tech timetable generator")
    ap.add_argument("--time", type=int, default=120,
                    help="solver time budget (seconds)")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--config", default=None, help="YAML config for weights")
    ap.add_argument("--section", default=None, help="print only this section")
    ap.add_argument("--json", action="store_true",
                    help="dump full result as JSON")
    args = ap.parse_args(argv)

    inst = build_sample()

    data_errors = inst.validate()
    if data_errors:
        print("DATA ERRORS:")
        for e in data_errors:
            print("  -", e)
        return 2

    issues = pre_solve_report(inst)
    if issues:
        print("PRE-SOLVE BOTTLENECKS DETECTED:")
        for it in issues:
            print(f"  [{it['kind']}] {it['resource']}: {it['suggestion']}")
        print("Attempting solve anyway...\n")

    weights = load_weights(args.config)
    solver = TimetableSolver(inst, weights, time_limit_s=args.time,
                             seed=args.seed)
    result = solver.solve()

    print(f"Solver status: {result.status}")
    if result.status not in ("OPTIMAL", "FEASIBLE"):
        diag = explain_infeasible(inst)
        print("INFEASIBLE -> diagnosis:")
        print(outputs.to_json(diag))
        return 1

    report = outputs.validate_placements(inst, result.placements)
    print(f"Hard-constraint violations: {report['hard_violations']}")
    if not report["valid"]:
        for d in report["details"]:
            print("  !", d)
    print(f"Soft-penalty objective: {result.soft_penalty}")

    if args.json:
        print(outputs.to_json({
            "status": result.status,
            "objective": result.objective,
            "validation": report,
            "placements": result.placements,
            "faculty": outputs.faculty_grids(inst, result.placements),
            "rooms": outputs.room_utilisation(inst, result.placements),
        }))
    else:
        print_section_grids(inst, result.placements, only=args.section)

    return 0 if report["valid"] else 3


if __name__ == "__main__":
    sys.exit(main())
