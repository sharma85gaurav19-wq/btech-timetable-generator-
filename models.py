"""
models.py
=========
Core data model for the B.Tech Automated Timetable Generator.

All entities are plain dataclasses so they can be loaded from JSON/CSV/Excel
and validated independently of the solver. Keeping the data model separate
from the constraint definitions and the solver is a non-negotiable build
principle (see README, section "Architecture").
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set, Tuple


class CourseType(str, Enum):
    THEORY = "THEORY"
    TUTORIAL = "TUTORIAL"
    LAB = "LAB"
    ELECTIVE = "ELECTIVE"
    PROJECT = "PROJECT"
    SEMINAR = "SEMINAR"
    MOOC = "MOOC"


class RoomType(str, Enum):
    THEORY = "THEORY"
    LAB = "LAB"
    DRAWING_HALL = "DRAWING_HALL"
    SEMINAR_HALL = "SEMINAR_HALL"


@dataclass
class Calendar:
    """Institution-wide time grid."""
    working_days: List[str]
    periods_per_day: int
    period_minutes: int
    # period indices (0-based) that are protected breaks/lunch and must stay free.
    # Staggered lunch is modelled per-year via 'lunch_by_year'.
    short_break_after_period: Optional[int] = None
    lunch_by_year: Dict[int, int] = field(default_factory=dict)

    def day_index(self, day: str) -> int:
        return self.working_days.index(day)

    @property
    def num_days(self) -> int:
        return len(self.working_days)

    def all_slots(self) -> List[Tuple[int, int]]:
        return [(d, p) for d in range(self.num_days)
                for p in range(self.periods_per_day)]


@dataclass
class Room:
    id: str
    room_type: RoomType
    capacity: int
    equipment: Set[str] = field(default_factory=set)
    owner_dept: Optional[str] = None
    shared: bool = True


@dataclass
class Faculty:
    id: str
    name: str
    department: str
    # (day_index, period_index) pairs the teacher is NOT available.
    unavailable: Set[Tuple[int, int]] = field(default_factory=set)
    max_load_per_day: int = 6
    max_load_per_week: int = 24
    preferred: Set[Tuple[int, int]] = field(default_factory=set)


@dataclass
class Section:
    id: str
    branch: str
    year: int
    name: str
    strength: int
    num_lab_batches: int = 1

    @property
    def batch_size(self) -> int:
        return -(-self.strength // max(1, self.num_lab_batches))  # ceil div


@dataclass
class Course:
    """A teaching requirement for one section in one semester."""
    id: str
    code: str
    title: str
    course_type: CourseType
    section_id: str
    # L-T-P credit structure
    lectures_per_week: int = 0
    tutorials_per_week: int = 0
    lab_sessions_per_week: int = 0
    lab_block_len: int = 2          # contiguous periods per lab session
    credits: int = 0
    eligible_faculty: List[str] = field(default_factory=list)
    required_room_type: RoomType = RoomType.THEORY
    required_equipment: Set[str] = field(default_factory=set)
    # Electives that share a common slot reference the same basket id.
    elective_basket: Optional[str] = None
    # Pre-placed / locked slots: list of (day_index, period_index).
    locked_slots: List[Tuple[int, int]] = field(default_factory=list)

    @property
    def total_weekly_hours(self) -> int:
        return (self.lectures_per_week
                + self.tutorials_per_week
                + self.lab_sessions_per_week * self.lab_block_len)


@dataclass
class Institute:
    calendar: Calendar
    sections: Dict[str, Section]
    courses: Dict[str, Course]
    faculty: Dict[str, Faculty]
    rooms: Dict[str, Room]
    one_subject_per_day_theory: bool = True

    # ---- validation -------------------------------------------------------
    def validate(self) -> List[str]:
        """Return a list of human-readable data errors (empty == clean)."""
        errors: List[str] = []
        for c in self.courses.values():
            if c.section_id not in self.sections:
                errors.append(f"Course {c.code}: unknown section '{c.section_id}'.")
            for f in c.eligible_faculty:
                if f not in self.faculty:
                    errors.append(f"Course {c.code}: unknown faculty '{f}'.")
            if not c.eligible_faculty and c.course_type not in (
                    CourseType.MOOC, CourseType.PROJECT):
                errors.append(f"Course {c.code}: no eligible faculty assigned.")
            if c.lab_sessions_per_week and c.lab_block_len < 1:
                errors.append(f"Course {c.code}: lab_block_len must be >= 1.")
        for s in self.sections.values():
            if s.strength <= 0:
                errors.append(f"Section {s.id}: strength must be positive.")
        return errors
