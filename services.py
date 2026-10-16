import json
from collections import defaultdict
from datetime import date as Date

import repository
from models import Invoice, Payment

UNKNOWN_VENDOR = "UNKNOWN"
CANCELLED = "CANCELLED"


class NotFoundError(ValueError):
    """A requested invoice or payment does not exist (HTTP 404)."""


class ConflictError(ValueError):
    """The request conflicts with existing data (HTTP 409)."""


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------
def _vendor_key(invoice: dict) -> str:
    vendor = (invoice.get("vendor_name") or "").strip().casefold()
    return vendor or UNKNOWN_VENDOR.casefold()


def _numeric_amount(invoice: dict) -> int | float | None:
    amount = invoice.get("invoice_amount")
    if isinstance(amount, bool) or not isinstance(amount, (int, float)):
        return None
    return amount


def _is_cancelled(invoice: dict) -> bool:
    return str(invoice.get("status") or "").upper() == CANCELLED


def _exact_key(invoice: dict) -> str:
    return json.dumps(invoice, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _parse_dates(invoices: list[dict], where) -> list[Date]:
    """Parse every invoice date; `where(i)` describes row i in the error message."""
    dates = []
    for index, invoice in enumerate(invoices):
        try:
            dates.append(Date.fromisoformat(invoice.get("date")))
        except (TypeError, ValueError) as error:
            raise ValueError(
                f"Invoice {where(index)} has an invalid or missing ISO date."
            ) from error
    return dates


def _group_exact_duplicates(invoices: list[dict]) -> list[list[int]]:
    """Index groups of rows whose fields are all equal (first index leads)."""
    groups = defaultdict(list)
    for index, invoice in enumerate(invoices):
        groups[_exact_key(invoice)].append(index)
    return [indexes for indexes in groups.values() if len(indexes) > 1]


def _find_suspected_duplicates(invoices: list[dict]) -> list[tuple[int, int]]:
    """Index pairs with the same vendor, amount and date but different invoice numbers."""
    groups = defaultdict(list)
    for index, invoice in enumerate(invoices):
        amount = _numeric_amount(invoice)
        if amount is not None:
            groups[(_vendor_key(invoice), amount, invoice.get("date"))].append(index)

    pairs = []
    for indexes in groups.values():
        for position, first in enumerate(indexes):
            for second in indexes[position + 1:]:
                if invoices[first].get("invoice_no") != invoices[second].get("invoice_no"):
                    pairs.append((first, second))
    return sorted(pairs)


def _match_credit_notes(
    invoices: list[dict], dates: list[Date]
) -> tuple[list[tuple[int, int]], list[int]]:
    """Match each credit note to the latest earlier unused invoice with the same
    vendor and absolute amount.

    Returns ((invoice_index, credit_note_index) pairs, unmatched credit note indexes).
    """
    positives = defaultdict(list)
    for index, invoice in enumerate(invoices):
        amount = _numeric_amount(invoice)
        if amount is not None and amount > 0:
            positives[(_vendor_key(invoice), amount)].append(index)

    used = set()
    pairs = []
    unmatched = []
    for credit_index, credit in enumerate(invoices):
        amount = _numeric_amount(credit)
        if amount is None or amount >= 0:
            continue
        candidates = [
            index
            for index in positives.get((_vendor_key(credit), -amount), ())
            if index not in used and dates[index] < dates[credit_index]
        ]
        if not candidates:
            unmatched.append(credit_index)
            continue
        invoice_index = max(candidates, key=dates.__getitem__)
        used.add(invoice_index)
        pairs.append((invoice_index, credit_index))
    return pairs, unmatched


def _sum_payments(payments: list[dict]) -> dict[str, float]:
    totals = {}
    for payment in payments:
        parsed = Payment(**payment)
        totals[parsed.invoice_no] = totals.get(parsed.invoice_no, 0) + parsed.paid
    return totals


def _reconcile(invoices: list[dict], paid_by_invoice: dict[str, float]):
    """Reconcile non-cancelled invoices that have an amount against payments.

    Each invoice number's payments are applied once (to its first listed row), so
    repeated invoice numbers never inflate the paid total.
    Returns (reconciliation rows, the invoices those rows describe).
    """
    remaining = dict(paid_by_invoice)
    rows = []
    included = []
    for invoice in invoices:
        amount = _numeric_amount(invoice)
        if amount is None or _is_cancelled(invoice):
            continue
        invoice_no = invoice.get("invoice_no")
        paid = remaining.pop(invoice_no, 0)
        rows.append(
            {
                "invoice_no": invoice_no,
                "invoice_amount": amount,
                "paid": paid,
                "balance": amount - paid,
            }
        )
        included.append(invoice)
    return rows, included


def _totals(rows: list[dict]) -> dict:
    invoice_total = sum(row["invoice_amount"] for row in rows)
    paid_total = sum(row["paid"] for row in rows)
    return {
        "invoice_total": invoice_total,
        "paid_total": paid_total,
        "balance_total": invoice_total - paid_total,
    }


def _print_report(report: dict) -> None:
    for row in report["missing_invoice_amount_rows"]:
        print(f"Invoice index {row} has no invoice amount; excluded from totals.")
    print(f"Exact duplicates: {report['exact_dupes']}")
    for pair in report["suspected_duplicates"]:
        print(f"{pair['invoice_nos'][0]} ~ {pair['invoice_nos'][1]}")
    for pair in report["credit_note_pairs"]:
        print(f"{pair[0]} <-> {pair[1]}")
    for row in report["unmatched_credit_note_rows"]:
        print(f"Credit note at index {row} has no partner.")
    print(f"Vendor totals: {report['vendor_totals']}")
    print(f"Payment reconciliation: {report['payment_reconciliation']}")


# --------------------------------------------------------------------------
# Analysis (read-only, rows numbered from 1)
# --------------------------------------------------------------------------
def analyze_invoice_rows(invoices: list[dict]) -> dict:
    vendor_variants = defaultdict(set)
    for row_number, invoice in enumerate(invoices, start=1):
        vendor = invoice.get("vendor_name") or UNKNOWN_VENDOR
        if not isinstance(vendor, str):
            raise ValueError(f"Invoice on row {row_number} has an invalid vendor.")
        vendor_variants[_vendor_key(invoice)].add(vendor)

    dates = _parse_dates(invoices, lambda index: f"on row {index + 1}")
    credit_pairs, unmatched = _match_credit_notes(invoices, dates)

    return {
        "vendor_groups": [
            {"vendor_names": sorted(names, key=lambda name: (name.casefold(), name))}
            for names in vendor_variants.values()
            if len(names) > 1
        ],
        "credit_notes": [
            {"credit_note_row": credit + 1, "canceled_invoice_row": invoice + 1}
            for invoice, credit in credit_pairs
        ],
        "unmatched_credit_notes": [{"row": index + 1} for index in unmatched],
        "exact_duplicates": [
            {"rows": [index + 1 for index in indexes]}
            for indexes in _group_exact_duplicates(invoices)
        ],
        "suspected_duplicates": [
            {"rows": [first + 1, second + 1]}
            for first, second in _find_suspected_duplicates(invoices)
        ],
    }


def analyze_invoices() -> dict:
    return analyze_invoice_rows(repository.load_invoices())


# --------------------------------------------------------------------------
# Processing
# --------------------------------------------------------------------------
def _normalize_invoices(invoices: list[dict]) -> None:
    """Strip/title-case vendors, upper-case statuses, validate field types."""
    for index, invoice in enumerate(invoices):
        vendor = invoice.get("vendor_name")
        if vendor is not None and not isinstance(vendor, str):
            raise ValueError(f"Invoice at index {index} has an invalid vendor.")
        invoice["vendor_name"] = (vendor or "").strip().title() or UNKNOWN_VENDOR.title()

        status = invoice.get("status")
        if status is not None and not isinstance(status, str):
            raise ValueError(f"Invoice at index {index} has an invalid status.")
        invoice["status"] = (status or "").upper()

        if invoice.get("invoice_amount") is not None and _numeric_amount(invoice) is None:
            raise ValueError(f"Invoice at index {index} has an invalid invoice amount.")


def process_invoice_rows(invoices: list[dict], payments: list[dict]) -> dict:
    """Normalize, deduplicate, cancel matched credit notes and reconcile payments.

    `invoices` is updated in place. Duplicate-related indexes (`exact_dupes`,
    `removed_duplicate_rows`) refer to the input rows; every other index refers to
    the resulting (deduplicated) list.
    """
    _normalize_invoices(invoices)

    duplicate_groups = _group_exact_duplicates(invoices)
    exact_dupes = {_exact_key(invoices[group[0]]): group for group in duplicate_groups}
    removed = {index for group in duplicate_groups for index in group[1:]}
    invoices[:] = [invoice for index, invoice in enumerate(invoices) if index not in removed]

    missing_amount_rows = [
        index for index, invoice in enumerate(invoices) if invoice.get("invoice_amount") is None
    ]
    suspected_duplicates = [
        {
            "invoice_nos": [invoices[first].get("invoice_no"), invoices[second].get("invoice_no")],
            "indexes": [first, second],
        }
        for first, second in _find_suspected_duplicates(invoices)
    ]

    dates = _parse_dates(invoices, lambda index: f"at index {index}")
    credit_pairs, unmatched = _match_credit_notes(invoices, dates)
    for invoice_index, credit_index in credit_pairs:
        invoices[invoice_index]["status"] = CANCELLED
        invoices[credit_index]["status"] = CANCELLED

    paid_by_invoice = _sum_payments(payments)
    reconciliation, included = _reconcile(invoices, paid_by_invoice)
    vendor_totals = {}
    for invoice, row in zip(included, reconciliation):
        vendor = invoice["vendor_name"]
        vendor_totals[vendor] = vendor_totals.get(vendor, 0) + row["invoice_amount"]

    known_invoice_nos = {invoice.get("invoice_no") for invoice in invoices}
    report = {
        "missing_invoice_amount_rows": missing_amount_rows,
        "exact_dupes": exact_dupes,
        "removed_duplicate_rows": sorted(removed),
        "suspected_duplicates": suspected_duplicates,
        "credit_note_pairs": credit_pairs,
        "unmatched_credit_note_rows": unmatched,
        "vendor_totals": vendor_totals,
        "payment_reconciliation": reconciliation,
        **_totals(reconciliation),
        "unmatched_payment_invoice_nos": sorted(set(paid_by_invoice) - known_invoice_nos),
    }
    _print_report(report)
    return report


def process_invoices() -> dict:
    invoices = repository.load_invoices()
    payments = repository.load_payments()
    report = process_invoice_rows(invoices, payments)
    repository.save_invoices(invoices)
    return report


def reconcile_payments() -> dict:
    invoices = repository.load_invoices()
    paid_by_invoice = _sum_payments(repository.load_payments())
    rows, _ = _reconcile(invoices, paid_by_invoice)
    return {"invoices": rows, **_totals(rows)}


# --------------------------------------------------------------------------
# Payments
# --------------------------------------------------------------------------
def _require_payable_invoice(invoice_no: str) -> None:
    invoice = repository.find_invoice_by_no(invoice_no)
    if invoice is None:
        raise NotFoundError(f"Invoice {invoice_no} not found.")
    if _is_cancelled(invoice):
        raise ConflictError(f"Invoice {invoice_no} is cancelled; payments are not allowed.")


def list_payments(invoice_no: str | None = None) -> list[dict]:
    payments = repository.load_payments()
    if invoice_no is None:
        return payments
    return [payment for payment in payments if payment.get("invoice_no") == invoice_no]


def get_payment(payment_index: int) -> Payment:
    payment = repository.find_payment_by_index(payment_index)
    if payment is None:
        raise NotFoundError(f"Payment at index {payment_index} not found.")
    return Payment(**payment)


def create_payment(payment_data: dict) -> Payment:
    payment = Payment(**payment_data)
    _require_payable_invoice(payment.invoice_no)
    repository.add_payment(payment.model_dump())
    return payment


def update_payment(payment_index: int, payment_data: dict) -> Payment:
    payment = Payment(**payment_data)
    _require_payable_invoice(payment.invoice_no)
    if not repository.update_payment(payment_index, payment.model_dump()):
        raise NotFoundError(f"Payment at index {payment_index} not found.")
    return payment


def delete_payment(payment_index: int) -> None:
    if not repository.delete_payment(payment_index):
        raise NotFoundError(f"Payment at index {payment_index} not found.")


# --------------------------------------------------------------------------
# Invoices
# --------------------------------------------------------------------------
def get_invoice(invoice_no: str) -> Invoice:
    invoice_data = repository.find_invoice_by_no(invoice_no)
    if invoice_data is None:
        raise NotFoundError(f"Invoice with number {invoice_no} not found.")
    return Invoice(**invoice_data)


def create_invoice(invoice_data: dict) -> Invoice:
    invoice = Invoice(**invoice_data)
    if repository.find_invoice_by_no(invoice.invoice_no) is not None:
        raise ConflictError(f"Invoice with number {invoice.invoice_no} already exists.")
    repository.add_invoice(invoice.model_dump())
    return invoice


def update_invoice(invoice_no: str, updated_data: dict) -> Invoice:
    invoice = Invoice(**updated_data)
    if invoice.invoice_no != invoice_no:
        raise ValueError("invoice_no in the body must match the invoice number in the URL.")
    if not repository.update_invoice(invoice_no, invoice.model_dump()):
        raise NotFoundError(f"Invoice with number {invoice_no} not found.")
    return invoice


def delete_invoice(invoice_no: str) -> None:
    copies = repository.count_invoices_with_no(invoice_no)
    if copies == 0:
        raise NotFoundError(f"Invoice with number {invoice_no} not found.")
    if copies == 1 and list_payments(invoice_no):
        raise ConflictError(
            f"Invoice {invoice_no} has recorded payments; delete the payments first."
        )
    repository.delete_invoice(invoice_no)
