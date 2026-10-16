import json
import os
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
DATA_FILE = DATA_DIR / "invoices.json"
PAYMENT_FILE = DATA_DIR / "payment.json"


def _read(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _write(path, rows):
    """Write via a temp file so a crash never leaves a half-written data file."""
    path = Path(path)
    temp_path = path.with_name(path.name + ".tmp")
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(rows, f, indent=4, ensure_ascii=False)
    os.replace(temp_path, path)


def _invoice_index(invoices, invoice_no):
    return next(
        (i for i, invoice in enumerate(invoices) if invoice["invoice_no"] == invoice_no),
        None,
    )


def _in_range(rows, index):
    return 0 <= index < len(rows)


def load_invoices():
    return _read(DATA_FILE)


def save_invoices(invoices):
    _write(DATA_FILE, invoices)


def load_payments():
    return _read(PAYMENT_FILE)


def save_payments(payments):
    _write(PAYMENT_FILE, payments)


def find_invoice_by_no(invoice_no):
    invoices = load_invoices()
    index = _invoice_index(invoices, invoice_no)
    return None if index is None else invoices[index]


def count_invoices_with_no(invoice_no):
    return sum(1 for invoice in load_invoices() if invoice["invoice_no"] == invoice_no)


def add_invoice(invoice):
    invoices = load_invoices()
    invoices.append(invoice)
    save_invoices(invoices)


def update_invoice(invoice_no, updated_invoice):
    invoices = load_invoices()
    index = _invoice_index(invoices, invoice_no)
    if index is None:
        return False
    invoices[index] = updated_invoice
    save_invoices(invoices)
    return True


def delete_invoice(invoice_no):
    invoices = load_invoices()
    index = _invoice_index(invoices, invoice_no)
    if index is None:
        return False
    del invoices[index]
    save_invoices(invoices)
    return True


def find_payment_by_index(payment_index):
    payments = load_payments()
    return payments[payment_index] if _in_range(payments, payment_index) else None


def add_payment(payment):
    payments = load_payments()
    payments.append(payment)
    save_payments(payments)
    return len(payments) - 1


def update_payment(payment_index, updated_payment):
    payments = load_payments()
    if not _in_range(payments, payment_index):
        return False
    payments[payment_index] = updated_payment
    save_payments(payments)
    return True


def delete_payment(payment_index):
    payments = load_payments()
    if not _in_range(payments, payment_index):
        return False
    del payments[payment_index]
    save_payments(payments)
    return True
