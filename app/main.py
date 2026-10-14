"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import re
import os
import json
from bson import json_util
from datetime import date
from pydantic import field_validator, model_validator

from datetime import datetime, timedelta, timezone
from typing import Optional
from decimal import Decimal, ROUND_HALF_UP
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.errors import DuplicateKeyError


from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from pymongo import MongoClient

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


@app.on_event("startup")
def create_indexes():
    db.employees.create_index(
        [("emp_code", 1)],
        unique=True,
        name="uq_employees_emp_code",
    )

    db.employees.create_index(
        [("department", 1), ("emp_code", 1)],
        name="idx_employees_department_emp_code",
    )

    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True,
        name="uq_attendance_emp_code_date",
    )

    db.attendance_logs.create_index(
        [("date", -1), ("emp_code", 1)],
        name="idx_attendance_date_emp_code",
    )

    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", -1)],
        name="idx_attendance_emp_code_date_desc",
    )

    db.attendance_logs.create_index(
        [("status", 1), ("date", -1)],
        name="idx_attendance_status_date",
    )



# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def truncate_to_seconds(value: datetime) -> datetime:
    """Remove fractional seconds before attendance calculations."""
    return value.replace(microsecond=0)


def compute_late_minutes(punch_in: datetime, shift_start: str) -> int:
    punch_in = truncate_to_seconds(punch_in)

    h, m = map(int, shift_start.split(":"))
    shift_start_at = punch_in.replace(
        hour=h, minute=m, second=0, microsecond=0
    )

    elapsed_seconds = int(
        (punch_in - shift_start_at).total_seconds()
    )

    # The 10-minute grace period is not considered late.
    if elapsed_seconds <= 10 * 60:
        return 0

    return elapsed_seconds // 60


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    punch_in = truncate_to_seconds(punch_in)
    punch_out = truncate_to_seconds(punch_out)

    seconds = int((punch_out - punch_in).total_seconds())
    hours = Decimal(seconds) / Decimal(3600)

    return float(
        hours.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )

def compute_overtime(
    punch_out: datetime,
    shift_start: str,
    shift_end: str,
    date_str: str,
) -> int:
    shift_date = datetime.fromisoformat(date_str).date()
    start_h, start_m = map(int, shift_start.split(":"))
    end_h, end_m = map(int, shift_end.split(":"))

    shift_end_at = datetime(
        shift_date.year,
        shift_date.month,
        shift_date.day,
        end_h,
        end_m,
        tzinfo=IST,
    )

    # Overnight shift: end time belongs to the following day.
    if shift_end <= shift_start:
        shift_end_at += timedelta(days=1)

    punch_out = truncate_to_seconds(punch_out)

    if punch_out.tzinfo is None:
        punch_out = punch_out.replace(tzinfo=IST)
    else:
        punch_out = punch_out.astimezone(IST)

    elapsed_seconds = int(
        (punch_out - shift_end_at).total_seconds()
    )

    # Overtime is counted only after at least 30 minutes.
    if elapsed_seconds < 30 * 60:
        return 0

    return elapsed_seconds // 60




# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #

class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

    @field_validator("emp_code")
    @classmethod
    def validate_emp_code(cls, value: str) -> str:
        if not re.fullmatch(r"EMP\d{4,6}", value):
            raise ValueError("emp_code must match EMP followed by 4-6 digits")
        return value

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        if not 1 <= len(value) <= 100:
            raise ValueError("name must contain 1-100 characters")
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        if len(value) > 120 or not re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+", value
        ):
            raise ValueError("Invalid email")
        return value

    @field_validator("department")
    @classmethod
    def validate_department(cls, value: str) -> str:
        if not 1 <= len(value) <= 50:
            raise ValueError("department must contain 1-50 characters")
        return value

    @field_validator("shift_start", "shift_end")
    @classmethod
    def validate_shift_time(cls, value: str) -> str:
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("Shift time must use HH:MM format")
        return value

    @field_validator("joined_on")
    @classmethod
    def validate_joined_on(cls, value: str) -> str:
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise ValueError("joined_on must use YYYY-MM-DD format")

        if parsed.isoformat() != value:
            raise ValueError("joined_on must use YYYY-MM-DD format")

        return value

    @model_validator(mode="after")
    def validate_shift_range(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")
        return self




class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"

    @field_validator("punched_at", mode="before")
    @classmethod
    def validate_punched_at(cls, value):
        if value is None:
            return value

        if type(value) is not int:
            raise ValueError("punched_at must be an integer epoch-millisecond value")

        if not 100000000000 <= value <= 4102444800000:
            raise ValueError("punched_at is outside the permitted epoch-millisecond range")

        return value

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in {"PRESENT", "WFH", "ON_DUTY"}:
            raise ValueError("status must be PRESENT, WFH, or ON_DUTY")
        return value


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None

    @field_validator("punched_at", mode="before")
    @classmethod
    def validate_punched_at(cls, value):
        if value is None:
            return value
        if type(value) is not int:
            raise ValueError(
                "punched_at must be an integer epoch-millisecond value"
            )
        if not 100000000000 <= value <= 4102444800000:
            raise ValueError(
                "punched_at is outside the permitted epoch-millisecond range"
            )
        return value


class AttendancePatchIn(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str
    regularized_by: str

    @field_validator("status")
    @classmethod
    def validate_status(cls, value):
        if value is not None and value not in {
            "PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"
        }:
            raise ValueError("Invalid attendance status")
        return value

    @field_validator("punch_in", "punch_out", mode="before")
    @classmethod
    def validate_timestamps(cls, value):
        if value is None:
            return value
        if type(value) is not int:
            raise ValueError(
                "Punch timestamps must be integer epoch milliseconds"
            )
        if not 100000000000 <= value <= 4102444800000:
            raise ValueError(
                "Punch timestamp is outside the permitted range"
            )
        return value

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value):
        if not 5 <= len(value) <= 200:
            raise ValueError("reason must contain 5-200 characters")
        return value

    @field_validator("regularized_by")
    @classmethod
    def validate_regularized_by(cls, value):
        if not 1 <= len(value) <= 50:
            raise ValueError("regularized_by must contain 1-50 characters")
        return value


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #

@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except PyMongoError:
        raise HTTPException(
            status_code=503,
            detail="MongoDB unavailable",
        )




@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    now = datetime.now(IST).replace(microsecond=0)
    doc["created_at"] = now
    try:
        result = db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")

    doc.pop("_id", None)
    doc["created_at"] = int(now.timestamp() * 1000)

    return doc




@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    q = {}

    if department is not None:
        q["department"] = department

    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)

    items = list(
        db.employees.find(q, {"_id": 0})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )

    for item in items:
        created_at = item.get("created_at")

        if isinstance(created_at, datetime):
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)

            item["created_at"] = int(created_at.timestamp() * 1000)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }




@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.punched_at is None:
        ts = datetime.now(timezone.utc).replace(microsecond=0)
    else:
        ts = datetime.fromtimestamp(
            body.punched_at / 1000, tz=timezone.utc
        ).replace(microsecond=0)

    punch_in_ist = ts.astimezone(IST)
    shift_start = emp["shift_start"]
    shift_end = emp["shift_end"]

    # Determine which IST calendar date the shift belongs to.
    attendance_date = punch_in_ist.date()

    if shift_end <= shift_start and punch_in_ist.strftime("%H:%M") < shift_end:
        attendance_date -= timedelta(days=1)

    date_str = attendance_date.isoformat()

    # Calculate lateness relative to the actual shift start.
    h, m = map(int, shift_start.split(":"))
    shift_start_at = datetime(
        attendance_date.year,
        attendance_date.month,
        attendance_date.day,
        h,
        m,
        tzinfo=IST,
    )

    late_minutes = compute_late_minutes(
    punch_in_ist.replace(tzinfo=None),
    shift_start
)

    doc = {
        "emp_code": body.emp_code,
        "date": date_str,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": late_minutes,
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="Already punched in for this attendance date",
        )

    # Return the exact fields required by the API contract.
    return {
        "emp_code": doc["emp_code"],
        "date": doc["date"],
        "status": doc["status"],
        "punch_in": int(ts.timestamp() * 1000),
        "punch_out": None,
        "work_hours": None,
        "late_minutes": doc["late_minutes"],
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.punched_at is None:
        ts = datetime.now(timezone.utc).replace(microsecond=0)
    else:
        ts = datetime.fromtimestamp(
            body.punched_at / 1000, tz=timezone.utc
        ).replace(microsecond=0)

    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {"$lte": ts},
        },
        sort=[("punch_in", -1)],
    )

    if record is None:
        raise HTTPException(status_code=404, detail="No punch-in found")

    if record.get("punch_out") is not None:
        raise HTTPException(status_code=409, detail="Already punched out")

    punch_in_time = record["punch_in"]
    if punch_in_time.tzinfo is None:
        punch_in_time = punch_in_time.replace(tzinfo=timezone.utc)

    elapsed = (ts - punch_in_time).total_seconds()
    if elapsed <= 0 or elapsed > 24 * 60 * 60:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be after punch-in and within 24 hours",
        )

    work_hours = compute_work_hours(punch_in_time, ts)
    overtime_minutes = compute_overtime(
        ts,
        emp["shift_start"],
        emp["shift_end"],
        record["date"],
    )
    half_day = work_hours < 4.50

    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_out": None,
        },
        {
            "$set": {
                "punch_out": ts,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day,
            }
        },
    )

    if result.modified_count != 1:
        raise HTTPException(status_code=409, detail="Already punched out")

    record.update({
        "punch_out": ts,
        "work_hours": work_hours,
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
    })
    record.pop("_id", None)

    for field in ("punch_in", "punch_out"):
        value = record.get(field)
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            record[field] = int(value.timestamp() * 1000)

    record.setdefault("late_minutes", 0)
    record.setdefault("history", [])

    return record





@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    q = {}

    if emp_code is not None:
        q["emp_code"] = emp_code

    if date_from is not None or date_to is not None:
        q["date"] = {}
        if date_from is not None:
            q["date"]["$gte"] = date_from
        if date_to is not None:
            q["date"]["$lte"] = date_to

    if status is not None:
        q["status"] = status

    skip = (page - 1) * page_size
    total = db.attendance_logs.count_documents(q)

    docs = list(
        db.attendance_logs.find(q, {"_id": 0})
        .sort([("date", -1), ("emp_code", 1)])
        .skip(skip)
        .limit(page_size)
    )

    timestamp_fields = {"punch_in", "punch_out"}

    for doc in docs:
        for field in timestamp_fields:
            value = doc.get(field)
            if isinstance(value, datetime):
                if value.tzinfo is None:
                    value = value.replace(tzinfo=timezone.utc)
                doc[field] = int(value.timestamp() * 1000)

        doc.setdefault("late_minutes", 0)
        doc.setdefault("overtime_minutes", 0)
        doc.setdefault("half_day", False)
        doc.setdefault("history", [])

        for entry in doc["history"]:
            at = entry.get("at")

            if isinstance(at, datetime):
                if at.tzinfo is None:
                    at = at.replace(tzinfo=timezone.utc)

                entry["at"] = int(at.timestamp() * 1000)

                

    return {
        "items": docs,
        "total": total,
        "page": page,
        "page_size": page_size,
    }

@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: str,
    body: AttendancePatchIn,
):
    try:
        parsed_date = datetime.strptime(date, "%Y-%m-%d").date()
        if parsed_date.isoformat() != date:
            raise ValueError
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid date format")

    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    record = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date,
    })
    if record is None:
        raise HTTPException(status_code=404, detail="Attendance record not found")

    supplied = body.model_fields_set
    old_status = record["status"]
    old_in = record.get("punch_in")
    old_out = record.get("punch_out")

    if body.status in {"ABSENT", "LEAVE"} and (
        "punch_in" in supplied or "punch_out" in supplied
    ):
        raise HTTPException(
            status_code=422,
            detail="ABSENT or LEAVE cannot be combined with punch times",
        )

    new_status = body.status if "status" in supplied else old_status
    new_in = (
        datetime.fromtimestamp(body.punch_in / 1000, timezone.utc)
        if "punch_in" in supplied and body.punch_in is not None
        else old_in
    )
    new_out = (
        datetime.fromtimestamp(body.punch_out / 1000, timezone.utc)
        if "punch_out" in supplied and body.punch_out is not None
        else old_out
    )

    if new_status in {"ABSENT", "LEAVE"}:
        new_in = None
        new_out = None

    if new_status in {"PRESENT", "WFH", "ON_DUTY"} and new_in is None:
        raise HTTPException(
            status_code=422,
            detail="Presence status requires punch_in",
        )

    if new_in is not None:
        if new_in.tzinfo is None:
            new_in = new_in.replace(tzinfo=timezone.utc)
        new_in = truncate_to_seconds(new_in)

        local_in = new_in.astimezone(IST)
        attendance_date = parsed_date
        if (
            emp["shift_end"] <= emp["shift_start"]
            and local_in.strftime("%H:%M") < emp["shift_end"]
        ):
            attendance_date += timedelta(days=1)

        if local_in.date() != attendance_date:
            raise HTTPException(
                status_code=422,
                detail="punch_in must belong to the attendance date",
            )

    if new_out is not None:
        if new_in is None:
            raise HTTPException(
                status_code=422,
                detail="punch_out requires punch_in",
            )
        if new_out.tzinfo is None:
            new_out = new_out.replace(tzinfo=timezone.utc)
        new_out = truncate_to_seconds(new_out)

        elapsed = (new_out - new_in).total_seconds()
        if elapsed <= 0 or elapsed > 24 * 60 * 60:
            raise HTTPException(
                status_code=422,
                detail="punch_out must be after punch_in and within 24 hours",
            )

    if new_status in {"ABSENT", "LEAVE"}:
        late_minutes = 0
        work_hours = None
        overtime_minutes = 0
        half_day = False
    else:
        late_minutes = compute_late_minutes(
            new_in.astimezone(IST).replace(tzinfo=None),
            emp["shift_start"],
        )
        if new_out is None:
            work_hours = None
            overtime_minutes = 0
            half_day = False
        else:
            work_hours = compute_work_hours(new_in, new_out)
            overtime_minutes = compute_overtime(
                new_out,
                emp["shift_start"],
                emp["shift_end"],
                date,
            )
            half_day = work_hours < 4.50

    old_values = {
        "status": old_status,
        "punch_in": old_in,
        "punch_out": old_out,
        "work_hours": record.get("work_hours"),
        "late_minutes": record.get("late_minutes", 0),
        "overtime_minutes": record.get("overtime_minutes", 0),
        "half_day": record.get("half_day", False),
    }
    new_values = {
        "status": new_status,
        "punch_in": new_in,
        "punch_out": new_out,
        "work_hours": work_hours,
        "late_minutes": late_minutes,
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
    }

    changes = {}
    for field, new_value in new_values.items():
        old_value = old_values[field]
        if field in {"punch_in", "punch_out"}:
            if old_value is not None and old_value.tzinfo is None:
                old_value = old_value.replace(tzinfo=timezone.utc)
            if new_value is not None and new_value.tzinfo is None:
                new_value = new_value.replace(tzinfo=timezone.utc)

        if old_value != new_value:
            def api_value(value):
                if isinstance(value, datetime):
                    if value.tzinfo is None:
                        value = value.replace(tzinfo=timezone.utc)
                    return int(value.timestamp() * 1000)
                return value

            changes[field] = {
                "from": api_value(old_value),
                "to": api_value(new_value),
            }

    if not changes:
        raise HTTPException(status_code=422, detail="No changes supplied")

    now = datetime.now(timezone.utc).replace(microsecond=0)
    history_entry = {
        "at": now,
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes,
    }

    update_fields = {
        "status": new_status,
        "punch_in": new_in,
        "punch_out": new_out,
        "work_hours": work_hours,
        "late_minutes": late_minutes,
        "overtime_minutes": overtime_minutes,
        "half_day": half_day,
    }

    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "history": record.get("history", []),
            "status": old_status,
            "punch_in": old_in,
            "punch_out": old_out,
        },
        {
            "$set": update_fields,
            "$push": {"history": history_entry},
        },
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance record changed concurrently; retry the request",
        )

    updated = db.attendance_logs.find_one(
        {"emp_code": emp_code, "date": date},
        {"_id": 0},
    )

    for field in ("punch_in", "punch_out"):
        value = updated.get(field)
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            updated[field] = int(value.timestamp() * 1000)

    for entry in updated.get("history", []):
        if isinstance(entry.get("at"), datetime):
            value = entry["at"]
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            entry["at"] = int(value.timestamp() * 1000)

    return updated


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str,
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
):
    # Validate that the employee exists.
    emp = db.employees.find_one({"emp_code": emp_code})
    if emp is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    # Parse the requested month.
    try:
        year, month_number = map(int, month.split("-"))
        month_start = date(year, month_number, 1)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid month")

    if month_number == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month_number + 1, 1)

    month_end = next_month - timedelta(days=1)

    # Working days run from the later of joined_on and month start
    # through the end of the requested month, Monday to Friday.
    joined_on = date.fromisoformat(emp["joined_on"])
    working_start = max(joined_on, month_start)
    working_days = 0

    if working_start <= month_end:
        current_day = working_start
        while current_day <= month_end:
            if current_day.weekday() < 5:
                working_days += 1
            current_day += timedelta(days=1)

    # Calculate monthly attendance totals in MongoDB.
    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lt": next_month.isoformat(),
                },
            }
        },
        {
            "$addFields": {
                "_attendance_day": {
                    "$dateFromString": {"dateString": "$date"}
                },
                "_half_day": {"$ifNull": ["$half_day", False]},
                "_late_minutes": {"$ifNull": ["$late_minutes", 0]},
                "_overtime_minutes": {
                    "$ifNull": ["$overtime_minutes", 0]
                },
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {
                                        "$in": [
                                            {"$dayOfWeek": "$_attendance_day"},
                                            [2, 3, 4, 5, 6],
                                        ]
                                    },
                                ]
                            },
                            {
                                "$cond": [
                                    "$_half_day",
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "LEAVE"]},
                            1,
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$_late_minutes", 0]},
                            1,
                            0,
                        ]
                    }
                },
                "total_late_minutes": {"$sum": "$_late_minutes"},
                "total_overtime_minutes": {
                    "$sum": "$_overtime_minutes"
                },
            }
        },
    ]

    results = list(db.attendance_logs.aggregate(pipeline))

    if results:
        summary = results[0]
    else:
        summary = {
            "present_days": 0,
            "leave_days": 0,
            "late_count": 0,
            "total_late_minutes": 0,
            "total_overtime_minutes": 0,
        }

    present_days = summary["present_days"]

    # Attendance percentage rounded to four decimal places,
    # using ROUND_HALF_UP.
    if working_days == 0:
        attendance_pct = None
    else:
        attendance_pct = float(
            (
                Decimal(str(present_days))
                / Decimal(working_days)
                * Decimal(100)
            ).quantize(
                Decimal("0.0001"),
                rounding=ROUND_HALF_UP,
            )
        )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": summary["leave_days"],
        "late_count": summary["late_count"],
        "total_late_minutes": summary["total_late_minutes"],
        "total_overtime_minutes": summary["total_overtime_minutes"],
        "attendance_pct": attendance_pct,
    }



@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
):
    try:
        year, month_number = map(int, month.split("-"))
        month_start = date(year, month_number, 1)

        if month_number == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month_number + 1, 1)

    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid month")

    month_end = next_month - timedelta(days=1)

    pipeline = [
        {
            "$match": {
                "joined_on": {"$lte": month_end.isoformat()}
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"employee_code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$employee_code"]},
                                    {"$gte": ["$date", month_start.isoformat()]},
                                    {"$lt": ["$date", next_month.isoformat()]}
                                ]
                            }
                        }
                    },
                    {
                        "$addFields": {
                            "_attendance_day": {
                                "$dateFromString": {"dateString": "$date"}
                            },
                            "_half_day": {"$ifNull": ["$half_day", False]},
                            "_late_minutes": {"$ifNull": ["$late_minutes", 0]},
                            "_overtime_minutes": {
                                "$ifNull": ["$overtime_minutes", 0]
                            }
                        }
                    }
                ],
                "as": "logs"
            }
        },
        {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$addToSet": "$emp_code"},
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$in": [{"$dayOfWeek": "$logs._attendance_day"}, [2, 3, 4, 5, 6]]}
                                ]
                            },
                            {"$cond": ["$logs._half_day", 0.5, 1]},
                            0
                        ]
                    }
                },
                "work_hours_values": {
                    "$push": {
                        "$cond": [
                            {
                                "$and": [
                                    {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                    {"$ne": [{"$ifNull": ["$logs.work_hours", None]}, None]}
                                ]
                            },
                            "$logs.work_hours",
                            None
                        ]
                    }
                },
                "late_count": {
                    "$sum": {"$cond": [{"$gt": ["$logs._late_minutes", 0]}, 1, 0]}
                },
                "total_late_minutes": {"$sum": "$logs._late_minutes"},
                "leave_count": {
                    "$sum": {"$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]}
                },
                "on_duty_count": {
                    "$sum": {"$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]}
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": {"$size": "$headcount"},
                "present_days": 1,
                "avg_work_hours": {
                    "$let": {
                        "vars": {
                            "valid_hours": {
                                "$filter": {
                                    "input": "$work_hours_values",
                                    "as": "hours",
                                    "cond": {"$ne": ["$$hours", None]}
                                }
                            }
                        },
                        "in": {
                            "$cond": [
                                {"$gt": [{"$size": "$$valid_hours"}, 0]},
                                {"$round": [{"$avg": "$$valid_hours"}, 2]},
                                None
                            ]
                        }
                    }
                },
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1
            }
        },
        {"$sort": {"department": 1}}
    ]

    items = list(db.employees.aggregate(pipeline))

    return {
        "month": month,
        "items": items
    }



@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    limit: int = Query(10, ge=1, le=50),
    department: Optional[str] = None
):
    try:
        year, month_number = map(int, month.split("-"))
        month_start = date(year, month_number, 1)
        if month_number == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month_number + 1, 1)
    except ValueError:
        raise HTTPException(status_code=422, detail="Invalid month")

    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lt": next_month.isoformat()
                }
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                            1,
                            0
                        ]
                    }
                }
            }
        },
        {"$match": {"total_late_minutes": {"$gt": 0}}},
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee"
            }
        },
        {"$unwind": "$employee"}
    ]

    if department is not None:
        pipeline.append({
            "$match": {"employee.department": department}
        })

    pipeline.extend([
        {
            "$sort": {
                "total_late_minutes": -1,
                "_id": 1
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {"$rank": {}}
                }
            }
        },
        {"$match": {"rank": {"$lte": limit}}},
        {
            "$project": {
                "_id": 0,
                "rank": 1,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "total_late_minutes": 1,
                "late_count": 1
            }
        },
        {"$sort": {"total_late_minutes": -1, "emp_code": 1}}
    ])

    items = list(db.attendance_logs.aggregate(pipeline))

    return {"month": month, "items": items}




@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: date = Query(..., alias="from"),
    to_date: date = Query(..., alias="to")
):
    if to_date < from_date:
        raise HTTPException(
            status_code=422,
            detail="'to' must be on or after 'from'"
        )

    day_count = (to_date - from_date).days + 1

    if day_count > 92:
        raise HTTPException(
            status_code=422,
            detail="Date range cannot exceed 92 days"
        )

    if db.employees.count_documents({"department": department}) == 0:
        raise HTTPException(status_code=404, detail="Unknown department")

    pipeline = [
        {"$match": {"department": department}},
        {
            "$group": {
                "_id": None,
                "employee_codes": {"$push": "$emp_code"}
            }
        },
        {
            "$project": {
                "_id": 0,
                "employee_codes": 1,
                "dates": {
                    "$map": {
                        "input": {"$range": [0, day_count]},
                        "as": "offset",
                        "in": {
                            "$dateAdd": {
                                "startDate": {
                                    "$dateFromString": {
                                        "dateString": from_date.isoformat()
                                    }
                                },
                                "unit": "day",
                                "amount": "$$offset"
                            }
                        }
                    }
                }
            }
        },
        {"$unwind": "$dates"},
        {
            "$lookup": {
                "from": "employees",
                "let": {"trend_date": "$dates"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$department", department]},
                                    {
                                        "$lte": [
                                            "$joined_on",
                                            {
                                                "$dateToString": {
                                                    "format": "%Y-%m-%d",
                                                    "date": "$$trend_date",
                                                    "timezone": "UTC"
                                                }
                                            }
                                        ]
                                    }
                                ]
                            }
                        }
                    },
                    {"$count": "count"}
                ],
                "as": "headcount_result"
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {
                    "trend_date": "$dates",
                    "employee_codes": "$employee_codes"
                },
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {
                                        "$eq": [
                                            "$date",
                                            {
                                                "$dateToString": {
                                                    "format": "%Y-%m-%d",
                                                    "date": "$$trend_date",
                                                    "timezone": "UTC"
                                                }
                                            }
                                        ]
                                    },
                                    {"$in": ["$emp_code", "$$employee_codes"]}
                                ]
                            }
                        }
                    },
                    {
                        "$group": {
                            "_id": None,
                            "present_count": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$in": [
                                                "$status",
                                                ["PRESENT", "WFH", "ON_DUTY"]
                                            ]
                                        },
                                        {
                                            "$cond": [
                                                {"$ifNull": ["$half_day", False]},
                                                0.5,
                                                1
                                            ]
                                        },
                                        0
                                    ]
                                }
                            },
                            "late_count": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$gt": [
                                                {"$ifNull": ["$late_minutes", 0]},
                                                0
                                            ]
                                        },
                                        1,
                                        0
                                    ]
                                }
                            }
                        }
                    }
                ],
                "as": "daily_logs"
            }
        },
        {
            "$addFields": {
                "headcount": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$headcount_result.count", 0]},
                        0
                    ]
                },
                "present_count": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$daily_logs.present_count", 0]},
                        0
                    ]
                },
                "late_count": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$daily_logs.late_count", 0]},
                        0
                    ]
                },
                "is_working_day": {
                    "$in": [{"$dayOfWeek": "$dates"}, [2, 3, 4, 5, 6]]
                }
            }
        },
        {
            "$addFields": {
                "attendance_rate": {
                    "$cond": [
                        {
                            "$and": [
                                "$is_working_day",
                                {"$gt": ["$headcount", 0]}
                            ]
                        },
                        {
                            "$round": [
                                {
                                    "$divide": [
                                        "$present_count",
                                        "$headcount"
                                    ]
                                },
                                4
                            ]
                        },
                        None
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"dates": 1},
                "output": {
                    "moving_avg_7d": {
                        "$avg": "$attendance_rate",
                        "window": {"documents": [-6, 0]}
                    }
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": {
                    "$dateToString": {
                        "format": "%Y-%m-%d",
                        "date": "$dates",
                        "timezone": "UTC"
                    }
                },
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": {"$round": ["$moving_avg_7d", 4]}
            }
        },
        {"$sort": {"date": 1}}
    ]

    items = list(db.employees.aggregate(pipeline))

    return {"department": department, "items": items}



@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str,
    emp_code: Optional[str] = None,
    month: Optional[str] = Query(
        None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$"
    ),
    department: Optional[str] = None,
    limit: int = Query(10, ge=1, le=50),
    date_from: Optional[date] = Query(None, alias="date_from"),
    date_to: Optional[date] = Query(None, alias="date_to"),
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    from_date: Optional[date] = Query(None, alias="from"),
    to_date: Optional[date] = Query(None, alias="to")
):
    allowed_endpoints = {
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend"
    }

    if endpoint not in allowed_endpoints:
        raise HTTPException(
            status_code=422,
            detail="Unsupported endpoint"
        )

    if endpoint in {
        "employee_monthly",
        "department_summary",
        "late_leaderboard"
    } and month is None:
        raise HTTPException(
            status_code=422,
            detail="month is required"
        )

    if endpoint == "employee_monthly" and not emp_code:
        raise HTTPException(
            status_code=422,
            detail="emp_code is required"
        )

    if endpoint == "department_trend":
        if not department or from_date is None or to_date is None:
            raise HTTPException(
                status_code=422,
                detail="department, from and to are required"
            )

        if to_date < from_date:
            raise HTTPException(
                status_code=422,
                detail="'to' must be on or after 'from'"
            )

        if (to_date - from_date).days + 1 > 92:
            raise HTTPException(
                status_code=422,
                detail="Date range cannot exceed 92 days"
            )

    if endpoint == "attendance_list":
        query = {}

        if emp_code:
            query["emp_code"] = emp_code
        if date_from:
            query["date"] = {"$gte": date_from.isoformat()}
        if date_to:
            query.setdefault("date", {})["$lte"] = date_to.isoformat()
        if status:
            query["status"] = status

        explain = db.command(
            "explain",
            {
                "find": "attendance_logs",
                "filter": query,
                "sort": {"date": -1, "emp_code": 1},
                "skip": (page - 1) * page_size,
                "limit": page_size
            },
            verbosity="executionStats"
        )
        collection = "attendance_logs"

    elif endpoint == "employee_monthly":
        year, month_number = map(int, month.split("-"))
        month_start = date(year, month_number, 1)

        if month_number == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month_number + 1, 1)

        pipeline = [
            {
                "$match": {
                    "emp_code": emp_code,
                    "date": {
                        "$gte": month_start.isoformat(),
                        "$lt": next_month.isoformat()
                    }
                }
            },
            {"$group": {"_id": "$emp_code", "records": {"$sum": 1}}}
        ]

        explain = db.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {}
            },
            verbosity="executionStats"
        )
        collection = "attendance_logs"

    elif endpoint == "department_summary":
        year, month_number = map(int, month.split("-"))
        month_start = date(year, month_number, 1)

        if month_number == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month_number + 1, 1)

        match = {
            "date": {
                "$gte": month_start.isoformat(),
                "$lt": next_month.isoformat()
            }
        }

        if department:
            match["department"] = department

        pipeline = [
            {"$match": match},
            {"$group": {"_id": "$department", "records": {"$sum": 1}}}
        ]

        explain = db.command(
            "explain",
            {
                "aggregate": "employees",
                "pipeline": pipeline,
                "cursor": {}
            },
            verbosity="executionStats"
        )
        collection = "employees"

    elif endpoint == "late_leaderboard":
        year, month_number = map(int, month.split("-"))
        month_start = date(year, month_number, 1)

        if month_number == 12:
            next_month = date(year + 1, 1, 1)
        else:
            next_month = date(year, month_number + 1, 1)

        pipeline = [
            {
                "$match": {
                    "date": {
                        "$gte": month_start.isoformat(),
                        "$lt": next_month.isoformat()
                    }
                }
            },
            {
                "$group": {
                    "_id": "$emp_code",
                    "total_late_minutes": {
                        "$sum": {"$ifNull": ["$late_minutes", 0]}
                    }
                }
            },
            {"$match": {"total_late_minutes": {"$gt": 0}}}
        ]

        explain = db.command(
            "explain",
            {
                "aggregate": "attendance_logs",
                "pipeline": pipeline,
                "cursor": {}
            },
            verbosity="executionStats"
        )
        collection = "attendance_logs"

    else:
        if db.employees.count_documents(
            {"department": department}
        ) == 0:
            raise HTTPException(
                status_code=404,
                detail="Unknown department"
            )

        start_string = from_date.isoformat()
        end_string = to_date.isoformat()

        pipeline = [
            {
                "$match": {
                    "department": department,
                    "joined_on": {"$lte": end_string}
                }
            },
            {
                "$lookup": {
                    "from": "attendance_logs",
                    "let": {"codes": "$emp_code"},
                    "pipeline": [
                        {
                            "$match": {
                                "date": {
                                    "$gte": start_string,
                                    "$lte": end_string
                                }
                            }
                        }
                    ],
                    "as": "logs"
                }
            },
            {"$limit": 1}
        ]

        explain = db.command(
            "explain",
            {
                "aggregate": "employees",
                "pipeline": pipeline,
                "cursor": {}
            },
            verbosity="executionStats"
        )
        collection = "employees"

    from bson.json_util import dumps, loads

    
    return {
        "endpoint": endpoint,
        "collection": collection,
        "explain": json.loads(
            json_util.dumps(explain)
        )
    }


