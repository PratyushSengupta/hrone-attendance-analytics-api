# DECISIONS.md

## 1. Indexes

I created a unique compound index on `(emp_code, date)` to prevent duplicate daily attendance records. Additional indexes support employee lookups, department filtering, date-based attendance queries, and status filtering. I would avoid redundant indexes because they increase storage requirements and write overhead.

## 2. Punch-in Race

Concurrent requests attempt to create the same employee's attendance record. MongoDB's unique compound index allows only one insertion. The successful request returns `201 Created`, while duplicate-key errors are handled as `409 Conflict`.

## 3. Ties

The leaderboard ranks employees by total late minutes. Employees tied at the cutoff can share the same rank, so all employees whose rank is within the requested limit are included. Consequently, the response may contain more employees than the limit. A secondary sort provides deterministic output ordering.

## 4. Headcount

The department summary starts with eligible employee documents and looks up their attendance records. Employees without matching attendance logs remain in the aggregation and contribute to headcount, but their missing logs are not treated as explicit absences.

## 5. Scaling

For 100 times more data, I would inspect query execution plans, index efficiency, and aggregation performance. Based on measured bottlenecks, I would consider archiving historical records, partitioning data, or introducing additional infrastructure to maintain query performance.