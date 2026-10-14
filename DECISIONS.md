# DECISIONS.md

## 1. Indexes
I created a unique index on `(emp_code, date)` to prevent duplicate daily attendance records. Other indexes support employee lookup, department filtering, date-based attendance queries, and status filtering. I would avoid adding redundant indexes because they increase write costs and storage usage.

## 2. Punch-in race
Both requests attempt to create the same employee's attendance record. MongoDB's unique compound index allows only one insertion. The successful request returns `201`, while the duplicate-key error is caught and returned as `409 Conflict`.

## 3. Ties
The leaderboard uses late minutes for ranking and includes employees whose rank is within the requested limit. Employees tied at the cutoff can therefore all be returned, so the result may contain more than `limit` employees. A secondary sort makes the output predictable.

## 4. Headcount
The department summary starts with eligible employee documents and uses a lookup for attendance records. Preserving employees without matching logs ensures they remain in the aggregation and contribute to headcount.

## 5. Scaling
For 100 times more data, I would review query execution plans and index selectivity, then consider partitioning or archiving historical attendance records if measurements showed they were necessary.
