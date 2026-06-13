"""
sample_data.py
==============
A realistic-but-small B.Tech instance used for demos and acceptance tests.

2 branches (CSE, ECE) x 1 year (2nd) x 2 sections each = 4 sections,
each with a mix of theory + lab + one shared open elective basket.
Small enough to solve in well under the time budget, big enough to exercise
every constraint family.
"""
from __future__ import annotations

from models import (Calendar, Course, CourseType, Faculty, Institute, Room,
                    RoomType, Section)


def build_sample() -> Institute:
    cal = Calendar(
        working_days=["Mon", "Tue", "Wed", "Thu", "Fri"],
        periods_per_day=7,
        period_minutes=55,
        short_break_after_period=2,
        lunch_by_year={2: 4},  # period index 4 is lunch for 2nd years
    )

    sections = {
        "CSE-2A": Section("CSE-2A", "CSE", 2, "A", 64, num_lab_batches=2),
        "CSE-2B": Section("CSE-2B", "CSE", 2, "B", 60, num_lab_batches=2),
        "ECE-2A": Section("ECE-2A", "ECE", 2, "A", 60, num_lab_batches=2),
        "ECE-2B": Section("ECE-2B", "ECE", 2, "B", 58, num_lab_batches=2),
    }

    faculty = {
        "F_RAO":   Faculty("F_RAO", "Dr. Rao", "CSE", max_load_per_week=18),
        "F_IYER":  Faculty("F_IYER", "Dr. Iyer", "CSE", max_load_per_week=18),
        "F_KHAN":  Faculty("F_KHAN", "Dr. Khan", "CSE", max_load_per_week=18,
                           unavailable={(2, 0), (2, 1), (2, 2)}),  # Wed mornings
        "F_NAIR":  Faculty("F_NAIR", "Dr. Nair", "ECE", max_load_per_week=18),
        "F_BOSE":  Faculty("F_BOSE", "Dr. Bose", "ECE", max_load_per_week=18),
        "F_DESAI": Faculty("F_DESAI", "Dr. Desai", "ECE", max_load_per_week=18),
        "F_GUEST": Faculty("F_GUEST", "Prof. Guest", "MGMT",
                           max_load_per_week=6,
                           # visiting: only available Fri (day index 4)
                           unavailable={(d, p) for d in range(4)
                                        for p in range(7)}),
    }

    rooms = {
        "R101": Room("R101", RoomType.THEORY, 70),
        "R102": Room("R102", RoomType.THEORY, 70),
        "R103": Room("R103", RoomType.THEORY, 70),
        "R104": Room("R104", RoomType.THEORY, 70),
        "LAB_CS1": Room("LAB_CS1", RoomType.LAB, 30,
                        equipment={"python", "linux"}, owner_dept="CSE"),
        "LAB_CS2": Room("LAB_CS2", RoomType.LAB, 30,
                        equipment={"python", "linux"}, owner_dept="CSE"),
        "LAB_EC1": Room("LAB_EC1", RoomType.LAB, 30,
                        equipment={"matlab", "scope"}, owner_dept="ECE"),
        "LAB_EC2": Room("LAB_EC2", RoomType.LAB, 30,
                        equipment={"matlab", "scope"}, owner_dept="ECE"),
        "SEM1": Room("SEM1", RoomType.SEMINAR_HALL, 120),
    }

    courses = {}

    def add(cid, code, title, ctype, sec, L=0, T=0, labs=0, blk=2,
            facs=None, rtype=RoomType.THEORY, equip=None, basket=None):
        courses[cid] = Course(
            id=cid, code=code, title=title, course_type=ctype, section_id=sec,
            lectures_per_week=L, tutorials_per_week=T,
            lab_sessions_per_week=labs, lab_block_len=blk, credits=L + labs,
            eligible_faculty=facs or [], required_room_type=rtype,
            required_equipment=set(equip or []), elective_basket=basket)

    # ---- CSE sections ----
    for sec in ("CSE-2A", "CSE-2B"):
        add(f"{sec}_DS", "CS201", "Data Structures", CourseType.THEORY, sec,
            L=3, T=1, facs=["F_RAO"])
        add(f"{sec}_OS", "CS202", "Operating Systems", CourseType.THEORY, sec,
            L=3, facs=["F_IYER"])
        add(f"{sec}_DBMS", "CS203", "DBMS", CourseType.THEORY, sec,
            L=3, facs=["F_KHAN"])
        add(f"{sec}_DSLAB", "CS291", "DS Lab", CourseType.LAB, sec,
            labs=1, blk=2, facs=["F_RAO"], rtype=RoomType.LAB,
            equip=["python", "linux"])

    # ---- ECE sections ----
    for sec in ("ECE-2A", "ECE-2B"):
        add(f"{sec}_SS", "EC201", "Signals & Systems", CourseType.THEORY, sec,
            L=3, T=1, facs=["F_NAIR"])
        add(f"{sec}_EDC", "EC202", "Electronic Devices", CourseType.THEORY, sec,
            L=3, facs=["F_BOSE"])
        add(f"{sec}_NW", "EC203", "Networks", CourseType.THEORY, sec,
            L=3, facs=["F_DESAI"])
        add(f"{sec}_SSLAB", "EC291", "Signals Lab", CourseType.LAB, sec,
            labs=1, blk=2, facs=["F_NAIR"], rtype=RoomType.LAB,
            equip=["matlab", "scope"])

    # ---- Open elective basket shared by ALL four sections ----
    # Same basket id -> must occupy identical slots across sections so any
    # student can pick any option without a clash. Taught by visiting faculty
    # (Friday-only) in the seminar hall.
    for sec in sections:
        add(f"{sec}_OE", "OE201", "Open Elective", CourseType.ELECTIVE, sec,
            L=2, facs=["F_GUEST"], rtype=RoomType.SEMINAR_HALL,
            basket="OE_BASKET_1")

    return Institute(
        calendar=cal, sections=sections, courses=courses,
        faculty=faculty, rooms=rooms, one_subject_per_day_theory=True)
