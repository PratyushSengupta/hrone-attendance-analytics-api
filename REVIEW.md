# REVIEW.md

## Defects Found in the Starter Code

| # | Where (function / endpoint) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `compute_late_minutes()` | Overnight shifts can produce incorrect late-minute calculations if a punch-in occurs after midnight and belongs to the previous day's shift. | Test an overnight shift such as `22:00–06:00` with a punch-in shortly before or after the scheduled start, including early-morning cases. | Account for overnight-shift timing when determining the scheduled shift start and calculating lateness. |
| 2 | `POST /attendance/punch-in` | Concurrent punch-in requests for the same employee and attendance date can attempt to create duplicate records. | Send multiple simultaneous punch-in requests for the same employee and date. Exactly one should succeed; the others should return a conflict response. | Create a unique compound index on `(emp_code, date)` and handle duplicate-key errors by returning HTTP `409`. |
| 3 | `POST /attendance/punch-out` | Concurrent punch-out requests can attempt to close the same attendance record more than once or overwrite computed values. | Send multiple simultaneous punch-out requests for one open attendance record. Exactly one should successfully close it. | Use a conditional database update that only succeeds while `punch_out` is still unset. Calculate and store the derived attendance fields as part of the successful operation. |
| 4 | `PATCH /attendance/{emp_code}/{date}` | Concurrent regularization requests can overwrite one another's changes or lose audit-history entries. | Submit concurrent regularization requests for the same attendance record and verify that each accepted correction is preserved in the audit history. | Use a conditional update and retry or conflict handling where necessary so that concurrent changes do not silently overwrite each other. Append one history entry for each accepted correction. |
| 5 | `GET /attendance` | Nested history timestamps can be returned in a different format from the API contract if only top-level timestamps are converted. | Retrieve an attendance record containing history and inspect `history[].at` and the nested `from`/`to` values for `punch_in` and `punch_out`. | Convert all BSON datetime values exposed by the API, including nested history timestamps and nested punch-time values, to epoch milliseconds. |
| 6 | `GET /admin/explain/{endpoint}` | A simplified explain query does not accurately represent the real endpoint's query or aggregation pipeline. It may omit filters, sorting, pagination, joins, or grouping stages. | Compare the explain output with the corresponding endpoint's actual database operation and inspect execution statistics for the expected index usage. | Build the explain operation from the same filters, sorting, pagination, and aggregation pipeline used by the corresponding endpoint. Run it with `executionStats`. |
| 7 | Analytics endpoints | Calculating analytics by loading all matching attendance records into Python can become inefficient and does not meet the requirement to use MongoDB aggregation. | Test analytics against a large attendance dataset and inspect whether aggregation is performed by MongoDB. | Use MongoDB aggregation pipelines for analytics calculations and add indexes appropriate to the filters and sorting used by the endpoints. |

## Items Reviewed but Not Classified as Defects

- **Unique attendance index:** The unique index on `(emp_code, date)` is intentional because the data model permits at most one attendance record per employee per attendance date.

- **String calendar dates:** Storing attendance dates as `YYYY-MM-DD` strings is intentional according to `DATA_MODEL.md`. Calendar dates are not instants and should not be converted into timestamps.

- **Epoch-millisecond API timestamps:** These are required by the API contract. MongoDB stores the corresponding instants as BSON datetimes, and the API converts them to epoch milliseconds.

- **No-op regularization:** Returning HTTP `422` when a PATCH request makes no actual changes is intentional, provided it matches the specified API contract.

- **Aggregation-based analytics:** Analytics must use MongoDB aggregation rather than retrieving all records and calculating results in Python.

- **Missing legacy fields:** Missing `history` and `half_day` fields are valid for legacy attendance records. They should be treated as an empty array and `false`, respectively, as specified in `DATA_MODEL.md`.

- **MongoDB `_id`:** The internal MongoDB `_id` is not the employee identifier. Employee records must be identified using `emp_code`, and `_id` must not be exposed through the API.

- **Missing attendance records:** A weekday without an attendance document is not equivalent to an explicit `ABSENT` record. Analytics must distinguish between an unrecorded day and a recorded absence.

## Verification Notes

- Verify that concurrent punch-in requests produce exactly one successful creation for the same employee and attendance date.
- Verify that concurrent punch-out requests produce exactly one successful closure of an open attendance record.
- Verify that concurrent regularizations preserve accepted changes and audit-history entries.
- Verify that all API timestamps, including nested history timestamps, use epoch milliseconds.
- Verify that analytics read stored derived values such as `work_hours`, `late_minutes`, `overtime_minutes`, and `half_day` instead of recalculating them from punch times.
- Verify that the required indexes are created at application startup.
- Verify that each supported `/admin/explain/{endpoint}` operation explains the corresponding real query or pipeline and reports execution statistics.
- Test overnight shifts, half-days, leave, absences, employees who join mid-month, missing legacy fields, and large datasets.
