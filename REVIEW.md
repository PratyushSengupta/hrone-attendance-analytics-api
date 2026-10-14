# Defects Found in the Starter Code

| # | Where (function / endpoint) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | `compute_late_minutes()` | Overnight shifts can produce incorrect lateness calculations when an early-morning punch-in belongs to the previous day's shift. | Test an overnight shift such as `22:00–06:00` with punch-ins around the scheduled shift start and after midnight. | Use the attendance date to determine the correct scheduled shift start, normalize timestamps to IST, and calculate lateness against that shift start. |
| 2 | `POST /attendance/punch-in` | Concurrent punch-in requests for the same employee and attendance date can attempt to create duplicate attendance records. | Send multiple simultaneous punch-in requests for the same employee and date. Exactly one should succeed; the others should receive HTTP `409`. | Enforce a unique compound index on `(emp_code, date)` and handle duplicate-key errors as HTTP `409` conflicts. |
| 3 | `POST /attendance/punch-out` | Concurrent punch-out requests can attempt to close the same attendance record more than once. | Send simultaneous punch-out requests for one open attendance record. Exactly one should successfully close it. | Use a conditional database update that only matches an attendance record whose `punch_out` is still unset. Calculate and persist derived attendance fields during the successful closure operation. |
| 4 | `PATCH /attendance/{emp_code}/{date}` | Concurrent regularization requests can conflict and risk losing updates or audit-history entries. | Submit concurrent PATCH requests for the same attendance record and inspect the final values and audit history. | Use a conditional update that checks existing record values and appends the corresponding history entry atomically. Handle update conflicts explicitly so concurrent changes are not silently overwritten. |
| 5 | `GET /attendance` and PATCH response serialization | Nested history timestamps can be returned in a different format from the API contract if only top-level timestamps are converted. | Retrieve an attendance record containing history and inspect `history[].at` and nested `changes` values, particularly `from` and `to` for `punch_in` and `punch_out`. | Convert BSON/Python datetime values exposed through history entries and nested changes to epoch milliseconds while preserving non-timestamp values. |
| 6 | `GET /admin/explain/{endpoint}` | A simplified explain query may not represent the actual query or aggregation pipeline executed by the corresponding endpoint. | Compare the explain operation with the endpoint's actual database operation and inspect execution statistics and index usage. | Construct explain operations using the corresponding endpoint's database query or pipeline and request execution statistics with `executionStats`. |
| 7 | Analytics endpoints | Calculating analytics by loading matching attendance documents into Python does not satisfy the requirement to perform analytics calculations in MongoDB and can be inefficient for large datasets. | Test analytics with a large attendance dataset and inspect the database operations used to calculate the results. | Use MongoDB aggregation pipelines for analytics calculations and create indexes appropriate to query filters and sorting operations. |

## Items Reviewed but Not Classified as Defects

- **Unique attendance index:** The unique compound index on `(emp_code, date)` is intentional because the data model permits at most one attendance record per employee per attendance date.

- **String calendar dates:** Storing attendance dates as `YYYY-MM-DD` strings is intentional according to `DATA_MODEL.md`. Calendar dates represent dates rather than instants and should not be converted into timestamps.

- **Epoch-millisecond API timestamps:** The API contract requires epoch milliseconds for instant-valued fields. MongoDB stores the corresponding instants as BSON datetimes, which must be converted during API serialization.

- **No-op regularization:** Returning HTTP `422` when a PATCH request makes no actual changes is intentional if this matches the specified API contract.

- **Aggregation-based analytics:** Analytics calculations must use MongoDB aggregation rather than retrieving all matching attendance records and calculating results in Python.

- **Missing legacy fields:** Missing `history` and `half_day` fields may occur in legacy attendance records. Handle them using the defaults specified in `DATA_MODEL.md`: an empty history array and `false`, respectively.

- **MongoDB `_id`:** The internal MongoDB `_id` is not the employee identifier. Employee records must be identified using `emp_code`, and internal `_id` values must not be exposed through the API.

- **Missing attendance records:** A weekday without an attendance document is not equivalent to an explicit `ABSENT` record. Analytics must distinguish between an unrecorded day and a recorded absence.

## Verification Notes

- Verify that simultaneous punch-in requests for the same employee and attendance date produce exactly one successful creation, with the remaining requests returning HTTP `409`.
- Verify that simultaneous punch-out requests against the same open attendance record produce exactly one successful closure.
- Verify that concurrent regularizations do not silently lose accepted updates or audit-history entries.
- Verify that all API timestamps use epoch milliseconds, including nested history timestamps and nested punch-time values.
- Verify that analytics use stored derived values such as `work_hours`, `late_minutes`, `overtime_minutes`, and `half_day` where required by the API contract.
- Verify that required indexes are created idempotently at application startup.
- Verify that each supported `/admin/explain/{endpoint}` operation explains the corresponding database query or aggregation pipeline and returns execution statistics.
- Verify index usage and the absence of collection scans where required using representative large datasets.
- Test overnight shifts, lateness grace-period boundaries, overtime thresholds, half-days, leave, absences, employees who join mid-month, missing legacy fields, date-range validation, and pagination.
- Verify analytics behavior for employees without attendance logs, weekends, missing attendance documents, and department headcounts.
- Verify timestamp truncation, work-hour rounding, and reported numeric precision against the specified API contract.