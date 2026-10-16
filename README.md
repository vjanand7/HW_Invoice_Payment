# Invoice Payment

A FastAPI service that manages **invoices** and **payments** (stored as JSON files), checks invoice
data for quality problems, and reconciles payments against what is owed.

It can:

- create, read, update and delete invoices and payments;
- **analyse** invoices without changing them: vendor-name variants, duplicates and credit notes;
- **process** invoices: clean the data, remove duplicates, cancel invoices that a credit note has
  reversed, and report totals per vendor;
- **reconcile** payments: for each open invoice, show the amount, what has been paid and the balance.

## Project structure

```
HW_Invoice_Payment/
├── main.py            # FastAPI routes and error handlers
├── models.py          # Invoice and Payment models (validation)
├── services.py        # Business rules: analysis, processing, reconciliation, CRUD
├── repository.py      # Reads/writes the JSON files
├── data/
│   ├── invoices.json  # Invoice data
│   └── payment.json   # Payment data
├── test_services.py   # Unit tests for the business rules
└── test_api_qa.py     # End-to-end QA tests for every endpoint
```

Requests flow `main.py` (HTTP) → `services.py` (rules) → `repository.py` (files).

## Run it

```bash
pip install fastapi uvicorn
cd HW_Invoice_Payment
uvicorn main:app --reload
```

Interactive docs: http://127.0.0.1:8000/docs

## Data fields

**Invoice** (`data/invoices.json`)

| Field | Type | Notes |
| --- | --- | --- |
| `invoice_no` | string | Required, non-empty |
| `vendor_name` | string | Defaults to `UNKNOWN` |
| `invoice_amount` | number or null | Negative = credit note; `null` = amount missing |
| `status` | string | e.g. `PENDING`, `PAID`, `CANCELLED` |
| `date` | string | Must be a valid ISO date (`YYYY-MM-DD`) |

**Payment** (`data/payment.json`)

| Field | Type | Notes |
| --- | --- | --- |
| `invoice_no` | string | Required; the invoice must exist and not be cancelled |
| `paid` | number | Must be greater than 0 |

## API

### Invoices

| Method | Path | Behavior |
| --- | --- | --- |
| `GET` | `/invoices/{invoice_no}` | Get an invoice (first match if the number is repeated in the file) |
| `POST` | `/invoices` | Create an invoice; `409` if the `invoice_no` already exists |
| `PUT` | `/invoices/{invoice_no}` | Replace an invoice; the body's `invoice_no` must match the URL (`422` otherwise) |
| `DELETE` | `/invoices/{invoice_no}` | Delete an invoice; `409` if it still has payments |
| `GET` | `/invoices/analysis` | Read-only data-quality analysis |
| `POST` | `/invoices/process` | Clean the data, save it, and reconcile payments |
| `GET` | `/invoices/reconciliation` | Current reconciliation without re-running processing |

### Payments

| Method | Path | Behavior |
| --- | --- | --- |
| `GET` | `/payments` | List all payments |
| `GET` | `/payments/by-invoice/{invoice_no}` | List payments for one invoice |
| `GET` | `/payments/{payment_index}` | Get a payment by its zero-based list index |
| `POST` | `/payments` | Add a payment |
| `PUT` | `/payments/{payment_index}` | Replace a payment by index |
| `DELETE` | `/payments/{payment_index}` | Delete a payment by index |

Payments are addressed by index because the records have no payment ID and one invoice can have
several payments. Deleting a payment shifts the indexes of the ones after it.

### Status codes

| Code | Meaning |
| --- | --- |
| `404` | Invoice or payment index not found |
| `409` | Duplicate `invoice_no`, payment on a cancelled invoice, or deleting an invoice that has payments |
| `422` | Invalid request body, invalid stored data, or a data file that is missing or unreadable |

## Business rules

### Analysis — `GET /invoices/analysis`

Rows are numbered from 1, matching their position in `data/invoices.json`. It reports:

- **Vendor groups:** names that are the same after trimming spaces and ignoring case.
- **Credit notes:** a negative invoice is matched to an unused positive invoice from the same
  vendor with the same absolute amount and an **earlier** date (the latest earlier one wins).
  Each invoice can be cancelled by only one credit note.
- **Unmatched credit notes:** negative invoices with no such partner (including same-day ones).
- **Exact duplicates:** rows where every field is equal.
- **Suspected duplicates:** same vendor, amount and date, but different `invoice_no`.

An invalid or missing date returns `422`.

### Processing — `POST /invoices/process`

1. Vendor names are stripped and title-cased (blank → `Unknown`); statuses are upper-cased.
2. Exact duplicate rows are removed, keeping the first.
3. Suspected duplicates are reported but **not** removed.
4. Matched invoice/credit-note pairs are both marked `CANCELLED`.
5. The cleaned data is saved to `data/invoices.json`.

Totals leave out rows with a missing amount and `CANCELLED` rows; unmatched credit notes stay in.
The response includes vendor totals, per-invoice reconciliation and the grand totals.

Indexes in the report: `exact_dupes` and `removed_duplicate_rows` use the original input positions.
All other indexes (`missing_invoice_amount_rows`, `unmatched_credit_note_rows`,
`credit_note_pairs`, `suspected_duplicates`) are zero-based in the saved, deduplicated list.

### Reconciliation

Payments are summed by `invoice_no` and applied **once** per invoice number (to its first listed
row), so a repeated invoice number never inflates the paid total. Payments for unknown invoice
numbers are listed in `unmatched_payment_invoice_nos`. `balance = invoice_amount - paid`; an
overpayment gives a negative balance.

---

# Testing

## How to run

```bash
pip install httpx
python -m unittest discover -p "test*.py" -v
```

**Result: 122 tests, all passing.**

| Suite | File | Tests |
| --- | --- | --- |
| Business-rule unit tests | `test_services.py` | 5 |
| Invoice CRUD | `test_api_qa.py` | 22 |
| Payment endpoints | `test_api_qa.py` | 19 |
| Analysis | `test_api_qa.py` | 23 |
| Processing | `test_api_qa.py` | 28 |
| Reconciliation | `test_api_qa.py` | 11 |
| Data integrity and robustness | `test_api_qa.py` | 14 |

## Test approach

- The QA suite calls the real FastAPI app through `TestClient`, so routing, validation, status
  codes and file persistence are all exercised.
- Each test gets its own temporary copy of the data files, so **`data/` is never modified** by
  running the tests.
- Most tests start from the sample data in `data/`, which deliberately contains a repeated invoice
  number, an exact duplicate, a suspected duplicate, a vendor spelled several ways, a missing
  amount, a credit note and an unknown payment. Edge cases use small purpose-built datasets.
- Each endpoint is checked for the normal case, boundaries, bad input, and that failed requests
  leave the data unchanged.

## Test cases

### Invoice CRUD (22)

| Area | Cases covered |
| --- | --- |
| Get | Found; invoice with null amount; unknown number → 404; number is case-sensitive; repeated number returns the first match |
| Create | Valid invoice is returned and saved; missing vendor defaults to `UNKNOWN`; null amount allowed; negative amount (credit note) allowed; each required field missing → 422; wrong types → 422; empty body and non-JSON body → 422; unknown fields are ignored and not stored |
| Update | Valid update persists; unknown number → 404; invalid body → 422; only the first of repeated numbers changes |
| Delete | Delete succeeds then the invoice is gone; delete twice and delete unknown → 404; invoice with payments → 409 until the payments are removed; only one of a repeated number is removed |
| Routing | `/invoices/analysis` and `/invoices/reconciliation` are not treated as invoice numbers |

### Payment endpoints (19)

| Area | Cases covered |
| --- | --- |
| List | All payments; empty file; filter by invoice; unknown invoice → empty list |
| Get | By index; first and last index; out of range, negative and huge index → 404; non-integer index → 422 |
| Create | Valid payment → 201 and saved; unknown invoice → 404; empty/missing/null fields and non-numeric `paid` → 422; numeric string accepted |
| Update | Valid update persists without changing the count; bad index, negative index and unknown invoice → 404; missing field → 422; data unchanged after each failure |
| Delete | Removes the payment and shifts later indexes; bad index → 404; non-integer → 422; delete all then list is empty |
| Routing and lifecycle | `/payments/by-invoice/...` is not shadowed by the index route; create → update → read → delete in sequence |

### Analysis (23)

| Area | Cases covered |
| --- | --- |
| Sample data | Full expected analysis of the sample data |
| Safety | Read-only (file unchanged); empty dataset returns empty results |
| Credit notes | Match to an earlier invoice; same date is unmatched; amount or vendor mismatch is unmatched; latest earlier invoice is chosen; one invoice is cancelled only once; decimal amounts; zero amounts; null amounts do not break analysis |
| Vendor names | Case and whitespace variants grouped; a single spelling is not a group; blank or null vendor treated as unknown |
| Duplicates | Exact duplicates are case-sensitive; three-way duplicates form one group; same number with different data is not a suspect; suspects need the same date and amount |
| Errors | Invalid dates (null, empty, text, impossible, wrong format, number) → 422 naming the row; missing date key → 422; non-text vendor → 422; corrupt JSON → 422; missing file does not return 500 |

### Processing (28)

| Area | Cases covered |
| --- | --- |
| Sample data | Expected report; saved file is normalized and deduplicated; running it twice gives the same result; payments file is untouched; empty dataset |
| Normalization | Vendor strip and title-case; missing, null and blank vendor → `Unknown`; status upper-cased, missing status → blank |
| Credit notes | Pair is cancelled and excluded from totals; credit note dated before its invoice stays unmatched and reduces the total; same-day credit note unmatched; two credit notes for one invoice; latest earlier invoice chosen; unmatched notes are printed |
| Duplicates | Duplicates found after normalization, first kept; suspected duplicates reported but not removed |
| Totals | Missing amount excluded but row kept; payments summed per invoice; overpayment gives a negative balance; unmatched payments reported, not counted; zero-amount invoice included; floating-point totals |
| Indexes | Reported indexes all point into the same saved list |
| Errors | Invalid amount type (text, boolean, list), vendor or status type, or date → 422 and **nothing is saved**; malformed payment record → 422; corrupt or missing file → 422 |

### Reconciliation (11)

| Cases covered |
| --- |
| Basic invoice / paid / balance and totals · does not modify files · cancelled and null-amount invoices excluded · `cancelled` recognized in any letter case · matches the output of `process` · a new payment shows up immediately · repeated invoice number does not double-count payments (reconciliation and processing) · paid total never exceeds recorded payments · malformed payment → 422 · missing file → 422 |

### Data integrity and robustness (14)

| Cases covered |
| --- |
| Bad date rejected on create · one bad invoice cannot break analysis for the rest · duplicate `invoice_no` rejected on create · update cannot change `invoice_no` to an existing one · negative and zero payments rejected · payment on a cancelled invoice rejected · invoice with payments cannot leave orphan payments · missing data file never returns 500 (get and create) · stored record with a missing field is not reported as 404 · Unicode vendor names round-trip · OpenAPI schema builds · unknown route → 404, wrong method → 405 |

### Unit tests (5) — `test_services.py`

| Test | Checks |
| --- | --- |
| Vendor normalization | Names equal after trimming/case are grouped and flagged as suspected duplicates |
| Credit notes | Only an earlier positive invoice is cancelled, and only once |
| Exact vs suspected duplicates | The two duplicate rules stay distinct |
| Full processing | Normalize, deduplicate, cancel, totals, reconciliation and printed output together |
| Payment operations | Payment needs an existing invoice; a missing index is handled |

## Defects found by QA and fixed

An earlier QA pass found 17 failing scenarios; all are now fixed and covered by the tests above.

| Severity | Defect | Fix |
| --- | --- | --- |
| High | Payments counted once per repeated invoice number, overstating the paid total | Payments are applied once per invoice number |
| High | An invoice with an invalid date could be saved and then break analysis and processing for everyone | Date validated as ISO on create/update |
| Medium | Zero and negative payments accepted | `paid` must be greater than 0 |
| Medium | Duplicate invoice numbers accepted on create; update could change the number | `409` on duplicate; body number must match the URL |
| Medium | Process report mixed original and deduplicated indexes | All indexes use the saved list (except the duplicate-row fields) |
| Medium | Missing data file returned `500` on some endpoints | Consistent `422` |
| Low | Deleting an invoice left orphan payments; payments allowed on cancelled invoices; stored bad record reported as `404`; `cancelled` only recognized in capitals | `409` on delete with payments; `409` on cancelled invoice; `422`; case-insensitive check |
