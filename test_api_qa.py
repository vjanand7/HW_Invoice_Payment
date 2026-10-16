"""QA suite for the Invoice Payment API.
"""
import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import repository
from main import app

REAL_DATA = Path(repository.__file__).resolve().parent / "data"
SEED_INVOICES = json.loads((REAL_DATA / "invoices.json").read_text(encoding="utf-8"))
SEED_PAYMENTS = json.loads((REAL_DATA / "payment.json").read_text(encoding="utf-8"))


def inv(invoice_no, vendor="Acme", amount=100, status="PENDING", date="2026-01-01"):
    return {
        "invoice_no": invoice_no,
        "vendor_name": vendor,
        "invoice_amount": amount,
        "status": status,
        "date": date,
    }


class ApiTestCase(unittest.TestCase):
    """Base: isolated data dir + client that surfaces 500s as responses."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.inv_file = self.dir / "invoices.json"
        self.pay_file = self.dir / "payment.json"
        patches = [
            patch.object(repository, "DATA_FILE", self.inv_file),
            patch.object(repository, "PAYMENT_FILE", self.pay_file),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)
        self.client = TestClient(app, raise_server_exceptions=False)
        self.load()

    def load(self, invoices=None, payments=None):
        self.inv_file.write_text(
            json.dumps(copy.deepcopy(SEED_INVOICES if invoices is None else invoices)),
            encoding="utf-8",
        )
        self.pay_file.write_text(
            json.dumps(copy.deepcopy(SEED_PAYMENTS if payments is None else payments)),
            encoding="utf-8",
        )

    def stored_invoices(self):
        return json.loads(self.inv_file.read_text(encoding="utf-8"))

    def stored_payments(self):
        return json.loads(self.pay_file.read_text(encoding="utf-8"))

    def process(self):
        with contextlib.redirect_stdout(io.StringIO()):
            return self.client.post("/invoices/process")


# --------------------------------------------------------------------------
# Invoice CRUD
# --------------------------------------------------------------------------
class InvoiceCrudTests(ApiTestCase):
    def test_get_invoice_found(self):
        r = self.client.get("/invoices/INV-104")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["vendor_name"], "ABC Ltd ")
        self.assertEqual(r.json()["invoice_amount"], 62000)

    def test_get_invoice_with_null_amount(self):
        r = self.client.get("/invoices/INV-106")
        self.assertEqual(r.status_code, 200)
        self.assertIsNone(r.json()["invoice_amount"])

    def test_get_invoice_not_found(self):
        r = self.client.get("/invoices/NOPE")
        self.assertEqual(r.status_code, 404)
        self.assertIn("NOPE", r.json()["detail"])

    def test_get_invoice_is_case_sensitive(self):
        self.assertEqual(self.client.get("/invoices/inv-104").status_code, 404)

    def test_get_invoice_with_duplicate_id_returns_first_match(self):
        r = self.client.get("/invoices/INV-102")
        self.assertEqual(r.json()["invoice_amount"], 142500)

    def test_create_invoice(self):
        body = inv("INV-900", "NewCo", 10.5, "PENDING", "2026-02-01")
        r = self.client.post("/invoices", json=body)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), body)
        self.assertEqual(self.stored_invoices()[-1], body)
        self.assertEqual(len(self.stored_invoices()), len(SEED_INVOICES) + 1)

    def test_create_invoice_default_vendor_is_unknown(self):
        body = {"invoice_no": "INV-901", "invoice_amount": 5, "status": "PENDING", "date": "2026-02-01"}
        r = self.client.post("/invoices", json=body)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["vendor_name"], "UNKNOWN")

    def test_create_invoice_null_amount_allowed(self):
        body = inv("INV-902", amount=None)
        self.assertEqual(self.client.post("/invoices", json=body).status_code, 200)

    def test_create_invoice_negative_amount_allowed_as_credit_note(self):
        self.assertEqual(self.client.post("/invoices", json=inv("CN-1", amount=-50)).status_code, 200)

    def test_create_invoice_missing_required_fields(self):
        for field in ("invoice_no", "invoice_amount", "status", "date"):
            body = inv("INV-903")
            del body[field]
            with self.subTest(missing=field):
                self.assertEqual(self.client.post("/invoices", json=body).status_code, 422)

    def test_create_invoice_wrong_types(self):
        for field, bad in (("invoice_amount", "abc"), ("invoice_no", ["x"]), ("vendor_name", None), ("status", 5)):
            body = inv("INV-904")
            body[field] = bad
            with self.subTest(field=field):
                self.assertEqual(self.client.post("/invoices", json=body).status_code, 422)

    def test_create_invoice_empty_body_and_non_json(self):
        self.assertEqual(self.client.post("/invoices", json={}).status_code, 422)
        self.assertEqual(self.client.post("/invoices", content="not json",
                                          headers={"content-type": "application/json"}).status_code, 422)

    def test_create_invoice_ignores_unknown_fields(self):
        body = {**inv("INV-905"), "hack": "x"}
        r = self.client.post("/invoices", json=body)
        self.assertEqual(r.status_code, 200)
        self.assertNotIn("hack", self.stored_invoices()[-1])

    def test_update_invoice(self):
        body = inv("INV-104", "ABC Ltd", 70000, "PAID", "2026-09-08")
        r = self.client.put("/invoices/INV-104", json=body)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["invoice_amount"], 70000)
        self.assertEqual(self.client.get("/invoices/INV-104").json()["invoice_amount"], 70000)

    def test_update_invoice_not_found(self):
        r = self.client.put("/invoices/NOPE", json=inv("NOPE"))
        self.assertEqual(r.status_code, 404)

    def test_update_invoice_invalid_body(self):
        r = self.client.put("/invoices/INV-104", json={"invoice_amount": "x"})
        self.assertEqual(r.status_code, 422)

    def test_update_only_touches_first_of_duplicate_ids(self):
        self.client.put("/invoices/INV-102", json=inv("INV-102", "Z", 1))
        rows = [i for i in self.stored_invoices() if i["invoice_no"] == "INV-102"]
        self.assertEqual([r["invoice_amount"] for r in rows], [1, 150000])

    def test_delete_invoice(self):
        r = self.client.delete("/invoices/INV-105")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/invoices/INV-105").status_code, 404)

    def test_delete_invoice_twice_and_not_found(self):
        self.client.delete("/invoices/INV-105")
        self.assertEqual(self.client.delete("/invoices/INV-105").status_code, 404)
        self.assertEqual(self.client.delete("/invoices/NOPE").status_code, 404)

    def test_delete_invoice_with_payments_is_blocked_until_payments_removed(self):
        r = self.client.delete("/invoices/INV-104")
        self.assertEqual(r.status_code, 409)
        self.assertEqual(self.client.get("/invoices/INV-104").status_code, 200)
        self.client.delete("/payments/1")  # the INV-104 payment
        self.assertEqual(self.client.delete("/invoices/INV-104").status_code, 200)

    def test_delete_one_of_duplicate_ids_removes_single_row(self):
        before = len(self.stored_invoices())
        self.client.delete("/invoices/INV-101")
        self.assertEqual(len(self.stored_invoices()), before - 1)

    def test_route_collision_analysis_is_not_treated_as_invoice_no(self):
        self.assertEqual(self.client.get("/invoices/analysis").status_code, 200)
        self.assertEqual(self.client.get("/invoices/reconciliation").status_code, 200)


# --------------------------------------------------------------------------
# Payment endpoints
# --------------------------------------------------------------------------
class PaymentEndpointTests(ApiTestCase):
    def test_list_payments(self):
        r = self.client.get("/payments")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), SEED_PAYMENTS)

    def test_list_payments_empty(self):
        self.load(payments=[])
        self.assertEqual(self.client.get("/payments").json(), [])

    def test_payments_by_invoice(self):
        self.load(payments=[{"invoice_no": "INV-101", "paid": 1},
                            {"invoice_no": "INV-104", "paid": 2},
                            {"invoice_no": "INV-101", "paid": 3}])
        r = self.client.get("/payments/by-invoice/INV-101")
        self.assertEqual(r.status_code, 200)
        self.assertEqual([p["paid"] for p in r.json()], [1, 3])

    def test_payments_by_unknown_invoice_returns_empty_list(self):
        r = self.client.get("/payments/by-invoice/NOPE")
        self.assertEqual((r.status_code, r.json()), (200, []))

    def test_get_payment_by_index(self):
        r = self.client.get("/payments/1")
        self.assertEqual(r.json(), {"invoice_no": "INV-104", "paid": 60000})

    def test_get_payment_first_and_last_index(self):
        self.assertEqual(self.client.get("/payments/0").status_code, 200)
        self.assertEqual(self.client.get(f"/payments/{len(SEED_PAYMENTS) - 1}").status_code, 200)

    def test_get_payment_out_of_range_and_negative(self):
        self.assertEqual(self.client.get(f"/payments/{len(SEED_PAYMENTS)}").status_code, 404)
        self.assertEqual(self.client.get("/payments/-1").status_code, 404)
        self.assertEqual(self.client.get("/payments/999999999").status_code, 404)

    def test_get_payment_non_integer_index(self):
        self.assertEqual(self.client.get("/payments/abc").status_code, 422)
        self.assertEqual(self.client.get("/payments/1.5").status_code, 422)

    def test_create_payment(self):
        r = self.client.post("/payments", json={"invoice_no": "INV-105", "paid": 99.5})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json(), {"invoice_no": "INV-105", "paid": 99.5})
        self.assertEqual(self.stored_payments()[-1], {"invoice_no": "INV-105", "paid": 99.5})
        self.assertEqual(len(self.stored_payments()), len(SEED_PAYMENTS) + 1)

    def test_create_payment_unknown_invoice(self):
        r = self.client.post("/payments", json={"invoice_no": "NOPE", "paid": 1})
        self.assertEqual(r.status_code, 404)
        self.assertEqual(len(self.stored_payments()), len(SEED_PAYMENTS))

    def test_create_payment_validation(self):
        cases = [
            {"invoice_no": "", "paid": 1},
            {"invoice_no": "INV-101"},
            {"paid": 1},
            {"invoice_no": "INV-101", "paid": "abc"},
            {"invoice_no": None, "paid": 1},
            {},
        ]
        for body in cases:
            with self.subTest(body=body):
                self.assertEqual(self.client.post("/payments", json=body).status_code, 422)
        self.assertEqual(len(self.stored_payments()), len(SEED_PAYMENTS))

    def test_create_payment_accepts_numeric_string(self):
        r = self.client.post("/payments", json={"invoice_no": "INV-101", "paid": "10"})
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["paid"], 10.0)

    def test_update_payment(self):
        r = self.client.put("/payments/0", json={"invoice_no": "INV-105", "paid": 5})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.stored_payments()[0], {"invoice_no": "INV-105", "paid": 5})
        self.assertEqual(len(self.stored_payments()), len(SEED_PAYMENTS))

    def test_update_payment_bad_index_and_unknown_invoice(self):
        ok = {"invoice_no": "INV-105", "paid": 5}
        self.assertEqual(self.client.put("/payments/99", json=ok).status_code, 404)
        self.assertEqual(self.client.put("/payments/-1", json=ok).status_code, 404)
        self.assertEqual(self.client.put("/payments/0", json={"invoice_no": "NOPE", "paid": 5}).status_code, 404)
        self.assertEqual(self.client.put("/payments/0", json={"invoice_no": "INV-105"}).status_code, 422)
        self.assertEqual(self.stored_payments(), SEED_PAYMENTS)

    def test_delete_payment_and_index_shift(self):
        r = self.client.delete("/payments/0")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.stored_payments(), SEED_PAYMENTS[1:])
        self.assertEqual(self.client.get("/payments/0").json()["invoice_no"], "INV-104")

    def test_delete_payment_bad_index(self):
        self.assertEqual(self.client.delete("/payments/99").status_code, 404)
        self.assertEqual(self.client.delete("/payments/-1").status_code, 404)
        self.assertEqual(self.client.delete("/payments/x").status_code, 422)
        self.assertEqual(self.stored_payments(), SEED_PAYMENTS)

    def test_delete_all_payments_then_list_empty(self):
        for _ in SEED_PAYMENTS:
            self.assertEqual(self.client.delete("/payments/0").status_code, 200)
        self.assertEqual(self.client.get("/payments").json(), [])
        self.assertEqual(self.client.delete("/payments/0").status_code, 404)

    def test_by_invoice_route_not_shadowed_by_index_route(self):
        self.assertEqual(self.client.get("/payments/by-invoice/INV-101").status_code, 200)

    def test_payment_crud_lifecycle(self):
        self.client.post("/payments", json={"invoice_no": "INV-105", "paid": 1})
        idx = len(self.stored_payments()) - 1
        self.client.put(f"/payments/{idx}", json={"invoice_no": "INV-105", "paid": 2})
        self.assertEqual(self.client.get(f"/payments/{idx}").json()["paid"], 2)
        self.client.delete(f"/payments/{idx}")
        self.assertEqual(self.client.get(f"/payments/{idx}").status_code, 404)


# --------------------------------------------------------------------------
# GET /invoices/analysis
# --------------------------------------------------------------------------
class AnalysisTests(ApiTestCase):
    def test_seed_data_analysis(self):
        r = self.client.get("/invoices/analysis")
        self.assertEqual(r.status_code, 200)
        a = r.json()
        groups = sorted(sorted(g["vendor_names"]) for g in a["vendor_groups"])
        self.assertEqual(groups, [["ABC Ltd", "ABC Ltd "], ["XYZ Corp", "xyz corp"]])
        self.assertEqual(a["exact_duplicates"], [{"rows": [1, 3]}])
        self.assertEqual(a["suspected_duplicates"], [{"rows": [1, 9]}, {"rows": [3, 9]}])
        # The Globex credit note (row 4, 09-07) predates its invoice (row 7, 09-09).
        self.assertEqual(a["credit_notes"], [])
        self.assertEqual(a["unmatched_credit_notes"], [{"row": 4}])

    def test_analysis_does_not_modify_data(self):
        before = self.inv_file.read_text(encoding="utf-8")
        self.client.get("/invoices/analysis")
        self.assertEqual(self.inv_file.read_text(encoding="utf-8"), before)

    def test_empty_dataset(self):
        self.load(invoices=[])
        a = self.client.get("/invoices/analysis").json()
        self.assertTrue(all(v == [] for v in a.values()))

    def test_credit_note_matched_to_earlier_invoice(self):
        self.load([inv("A", amount=100, date="2026-01-01"), inv("B", amount=-100, date="2026-01-02")])
        a = self.client.get("/invoices/analysis").json()
        self.assertEqual(a["credit_notes"], [{"credit_note_row": 2, "canceled_invoice_row": 1}])
        self.assertEqual(a["unmatched_credit_notes"], [])

    def test_credit_note_same_date_is_unmatched(self):
        self.load([inv("A", amount=100), inv("B", amount=-100)])
        a = self.client.get("/invoices/analysis").json()
        self.assertEqual(a["unmatched_credit_notes"], [{"row": 2}])

    def test_credit_note_amount_or_vendor_mismatch_is_unmatched(self):
        self.load([inv("A", "Acme", 100, date="2026-01-01"),
                   inv("B", "Acme", -99, date="2026-01-02"),
                   inv("C", "Other", -100, date="2026-01-02")])
        a = self.client.get("/invoices/analysis").json()
        self.assertEqual(a["credit_notes"], [])
        self.assertEqual(a["unmatched_credit_notes"], [{"row": 2}, {"row": 3}])

    def test_credit_note_picks_latest_earlier_invoice(self):
        self.load([inv("A", date="2026-01-01"), inv("B", date="2026-01-05"),
                   inv("C", amount=-100, date="2026-01-10")])
        a = self.client.get("/invoices/analysis").json()
        self.assertEqual(a["credit_notes"], [{"credit_note_row": 3, "canceled_invoice_row": 2}])

    def test_one_invoice_cannot_be_cancelled_twice(self):
        self.load([inv("A", date="2026-01-01"), inv("B", amount=-100, date="2026-01-02"),
                   inv("C", amount=-100, date="2026-01-03")])
        a = self.client.get("/invoices/analysis").json()
        self.assertEqual(len(a["credit_notes"]), 1)
        self.assertEqual(a["unmatched_credit_notes"], [{"row": 3}])

    def test_float_amount_credit_matching(self):
        self.load([inv("A", amount=10.25, date="2026-01-01"), inv("B", amount=-10.25, date="2026-01-02")])
        self.assertEqual(len(self.client.get("/invoices/analysis").json()["credit_notes"]), 1)

    def test_zero_amount_is_neither_credit_nor_invoice_match(self):
        self.load([inv("A", amount=0, date="2026-01-01"), inv("B", amount=0, date="2026-01-02")])
        a = self.client.get("/invoices/analysis").json()
        self.assertEqual((a["credit_notes"], a["unmatched_credit_notes"]), ([], []))

    def test_null_amount_rows_do_not_break_analysis(self):
        self.load([inv("A", amount=None), inv("B", amount=None)])
        self.assertEqual(self.client.get("/invoices/analysis").status_code, 200)

    def test_vendor_case_and_whitespace_grouping(self):
        self.load([inv("A", "Acme"), inv("B", "ACME  "), inv("C", " acme")])
        groups = self.client.get("/invoices/analysis").json()["vendor_groups"]
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(groups[0]["vendor_names"]), 3)

    def test_single_vendor_spelling_is_not_a_group(self):
        self.load([inv("A", "Acme"), inv("B", "Acme")])
        self.assertEqual(self.client.get("/invoices/analysis").json()["vendor_groups"], [])

    def test_invalid_dates_return_422(self):
        for bad in (None, "", "not-a-date", "2026-13-45", "01/02/2026", 20260101):
            with self.subTest(date=bad):
                self.load([inv("A", date=bad)])
                r = self.client.get("/invoices/analysis")
                self.assertEqual(r.status_code, 422)
                self.assertIn("row 1", r.json()["detail"])

    def test_missing_date_key_returns_422(self):
        row = inv("A")
        del row["date"]
        self.load([row])
        self.assertEqual(self.client.get("/invoices/analysis").status_code, 422)

    def test_invalid_vendor_type_returns_422(self):
        self.load([inv("A", vendor=123)])
        self.assertEqual(self.client.get("/invoices/analysis").status_code, 422)

    def test_blank_vendor_treated_as_unknown(self):
        self.load([inv("A", vendor=""), inv("B", vendor=None)])
        self.assertEqual(self.client.get("/invoices/analysis").status_code, 200)

    def test_exact_duplicate_is_case_sensitive(self):
        self.load([inv("A", "Acme"), inv("A", "ACME")])
        self.assertEqual(self.client.get("/invoices/analysis").json()["exact_duplicates"], [])

    def test_three_way_exact_duplicate_grouped_together(self):
        self.load([inv("A"), inv("A"), inv("A")])
        self.assertEqual(self.client.get("/invoices/analysis").json()["exact_duplicates"],
                         [{"rows": [1, 2, 3]}])

    def test_same_invoice_no_with_different_data_is_not_suspected(self):
        self.load([inv("A", amount=100), inv("A", amount=100, status="PAID")])
        self.assertEqual(self.client.get("/invoices/analysis").json()["suspected_duplicates"], [])

    def test_suspected_duplicate_needs_same_date_and_amount(self):
        self.load([inv("A", date="2026-01-01"), inv("B", date="2026-01-02"), inv("C", amount=101)])
        self.assertEqual(self.client.get("/invoices/analysis").json()["suspected_duplicates"], [])

    def test_corrupt_json_returns_422(self):
        self.inv_file.write_text("{ broken", encoding="utf-8")
        self.assertEqual(self.client.get("/invoices/analysis").status_code, 422)

    def test_missing_invoice_file_does_not_crash_analysis(self):
        self.inv_file.unlink()
        self.assertNotEqual(self.client.get("/invoices/analysis").status_code, 500)


# --------------------------------------------------------------------------
# POST /invoices/process
# --------------------------------------------------------------------------
class ProcessTests(ApiTestCase):
    def test_seed_data_report(self):
        r = self.process()
        self.assertEqual(r.status_code, 200)
        rep = r.json()
        self.assertEqual(rep["missing_invoice_amount_rows"], [6])
        self.assertEqual(rep["removed_duplicate_rows"], [2])
        self.assertEqual(list(rep["exact_dupes"].values()), [[0, 2]])
        self.assertEqual(len(rep["suspected_duplicates"]), 1)
        self.assertEqual(rep["suspected_duplicates"][0]["invoice_nos"], ["INV-101", "INV-107"])
        self.assertEqual(rep["credit_note_pairs"], [])
        self.assertEqual(rep["unmatched_credit_note_rows"], [2])
        self.assertEqual(rep["vendor_totals"], {"Abc Ltd": 232000, "Xyz Corp": 292500, "Globex": 0})
        self.assertEqual(rep["invoice_total"], 524500)
        self.assertEqual(rep["paid_total"], 145000)
        self.assertEqual(rep["balance_total"], 379500)
        self.assertEqual(rep["unmatched_payment_invoice_nos"], ["INV-109"])

    def test_seed_data_persisted_normalized_and_deduplicated(self):
        self.process()
        rows = self.stored_invoices()
        self.assertEqual(len(rows), len(SEED_INVOICES) - 1)
        for row in rows:
            self.assertEqual(row["status"], row["status"].upper())
            self.assertEqual(row["vendor_name"], row["vendor_name"].strip().title())
        self.assertEqual([r["invoice_no"] for r in rows].count("INV-101"), 1)

    def test_process_is_idempotent(self):
        first = self.process().json()
        snapshot = self.stored_invoices()
        second = self.process().json()
        self.assertEqual(self.stored_invoices(), snapshot)
        for key in ("invoice_total", "paid_total", "balance_total", "vendor_totals",
                    "unmatched_credit_note_rows", "credit_note_pairs"):
            self.assertEqual(first[key], second[key], key)
        self.assertEqual(second["removed_duplicate_rows"], [])

    def test_process_does_not_touch_payments(self):
        self.process()
        self.assertEqual(self.stored_payments(), SEED_PAYMENTS)

    def test_empty_dataset(self):
        self.load(invoices=[], payments=[])
        rep = self.process().json()
        self.assertEqual((rep["invoice_total"], rep["paid_total"], rep["balance_total"]), (0, 0, 0))
        self.assertEqual(self.stored_invoices(), [])

    def test_vendor_title_case_and_strip(self):
        self.load([inv("A", "  aCME ltd  ")])
        self.process()
        self.assertEqual(self.stored_invoices()[0]["vendor_name"], "Acme Ltd")

    def test_missing_or_blank_vendor_becomes_unknown(self):
        row = inv("A")
        del row["vendor_name"]
        self.load([row, inv("B", vendor=None), inv("C", vendor="")])
        self.process()
        self.assertEqual([r["vendor_name"] for r in self.stored_invoices()], ["Unknown"] * 3)

    def test_status_uppercased_and_missing_status_blank(self):
        row = inv("B")
        del row["status"]
        self.load([inv("A", status="pending"), row])
        self.process()
        self.assertEqual([r["status"] for r in self.stored_invoices()], ["PENDING", ""])

    def test_credit_note_pair_cancelled_and_excluded_from_totals(self):
        self.load([inv("A", amount=100, date="2026-01-01"),
                   inv("B", "ACME ", -100, date="2026-01-02"),
                   inv("C", "Other", 40)], payments=[])
        rep = self.process().json()
        self.assertEqual(rep["credit_note_pairs"], [[0, 1]])
        self.assertEqual([r["status"] for r in self.stored_invoices()], ["CANCELLED", "CANCELLED", "PENDING"])
        self.assertEqual(rep["invoice_total"], 40)
        self.assertEqual([p["invoice_no"] for p in rep["payment_reconciliation"]], ["C"])

    def test_credit_note_before_invoice_stays_unmatched_and_reduces_total(self):
        self.load([inv("A", amount=-100, date="2026-01-01"), inv("B", amount=100, date="2026-01-02")], payments=[])
        rep = self.process().json()
        self.assertEqual(rep["unmatched_credit_note_rows"], [0])
        self.assertEqual(rep["invoice_total"], 0)
        self.assertEqual([r["status"] for r in self.stored_invoices()], ["PENDING", "PENDING"])

    def test_credit_note_same_day_unmatched(self):
        self.load([inv("A"), inv("B", amount=-100)], payments=[])
        self.assertEqual(self.process().json()["unmatched_credit_note_rows"], [1])

    def test_two_credit_notes_one_invoice(self):
        self.load([inv("A", date="2026-01-01"), inv("B", amount=-100, date="2026-01-02"),
                   inv("C", amount=-100, date="2026-01-03")], payments=[])
        rep = self.process().json()
        self.assertEqual(len(rep["credit_note_pairs"]), 1)
        self.assertEqual(rep["unmatched_credit_note_rows"], [2])
        self.assertEqual(rep["invoice_total"], -100)

    def test_credit_note_picks_latest_earlier_invoice(self):
        self.load([inv("A", date="2026-01-01"), inv("B", date="2026-01-05"),
                   inv("C", amount=-100, date="2026-01-10")], payments=[])
        self.assertEqual(self.process().json()["credit_note_pairs"], [[1, 2]])

    def test_missing_amount_excluded_from_totals_but_kept(self):
        self.load([inv("A", amount=None), inv("B", amount=10)], payments=[])
        rep = self.process().json()
        self.assertEqual(rep["missing_invoice_amount_rows"], [0])
        self.assertEqual(rep["invoice_total"], 10)
        self.assertEqual(len(self.stored_invoices()), 2)

    def test_invalid_amount_type_returns_422_and_does_not_save(self):
        for bad in ("100", True, [1]):
            with self.subTest(amount=bad):
                self.load([inv("A", amount=bad)])
                before = self.inv_file.read_text(encoding="utf-8")
                self.assertEqual(self.process().status_code, 422)
                self.assertEqual(self.inv_file.read_text(encoding="utf-8"), before)

    def test_invalid_vendor_or_status_type_returns_422(self):
        self.load([inv("A", vendor=5)])
        self.assertEqual(self.process().status_code, 422)
        self.load([inv("A", status=5)])
        self.assertEqual(self.process().status_code, 422)

    def test_invalid_date_returns_422_and_does_not_save(self):
        self.load([inv("A", date="bad"), inv("A", date="bad")])
        before = self.inv_file.read_text(encoding="utf-8")
        self.assertEqual(self.process().status_code, 422)
        self.assertEqual(self.inv_file.read_text(encoding="utf-8"), before)

    def test_malformed_payment_record_returns_422(self):
        self.load(payments=[{"invoice_no": "INV-101"}])
        self.assertEqual(self.process().status_code, 422)

    def test_corrupt_or_missing_files_return_422(self):
        self.inv_file.write_text("[[", encoding="utf-8")
        self.assertEqual(self.process().status_code, 422)
        self.load()
        self.pay_file.unlink()
        self.assertEqual(self.process().status_code, 422)

    def test_payments_summed_per_invoice(self):
        self.load([inv("A", amount=100)], payments=[{"invoice_no": "A", "paid": 30},
                                                    {"invoice_no": "A", "paid": 20.5}])
        rep = self.process().json()
        self.assertEqual(rep["payment_reconciliation"],
                         [{"invoice_no": "A", "invoice_amount": 100, "paid": 50.5, "balance": 49.5}])

    def test_overpayment_gives_negative_balance(self):
        self.load([inv("A", amount=100)], payments=[{"invoice_no": "A", "paid": 150}])
        self.assertEqual(self.process().json()["balance_total"], -50)

    def test_unmatched_payments_reported_not_counted(self):
        self.load([inv("A", amount=100)], payments=[{"invoice_no": "Z", "paid": 7}])
        rep = self.process().json()
        self.assertEqual(rep["unmatched_payment_invoice_nos"], ["Z"])
        self.assertEqual(rep["paid_total"], 0)

    def test_zero_amount_invoice_included(self):
        self.load([inv("A", amount=0)], payments=[])
        rep = self.process().json()
        self.assertEqual(len(rep["payment_reconciliation"]), 1)

    def test_floating_point_totals(self):
        self.load([inv("A", amount=0.1), inv("B", amount=0.2, vendor="Other")], payments=[])
        self.assertAlmostEqual(self.process().json()["invoice_total"], 0.3)

    def test_unmatched_credit_notes_printed(self):
        self.load([inv("A", amount=-5)], payments=[])
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            self.client.post("/invoices/process")
        self.assertIn("Credit note at index 0 has no partner", buf.getvalue())

    def test_exact_duplicates_after_normalisation_removed_keeping_first(self):
        self.load([inv("A", "acme", status="pending"), inv("A", "ACME ", status="PENDING")], payments=[])
        rep = self.process().json()
        self.assertEqual(rep["removed_duplicate_rows"], [1])
        self.assertEqual(len(self.stored_invoices()), 1)

    def test_suspected_duplicates_reported_but_not_removed(self):
        self.load([inv("A"), inv("B")], payments=[])
        rep = self.process().json()
        self.assertEqual(len(rep["suspected_duplicates"]), 1)
        self.assertEqual(len(self.stored_invoices()), 2)

    def test_reported_indexes_refer_to_one_consistent_list(self):
        # README: missing-amount rows are reported by zero-based index.  After a
        # duplicate is removed, "index" should mean the same list everywhere.
        self.load([inv("D"), inv("D"), inv("N", amount=None, vendor="N"),
                   inv("C", amount=-5, vendor="C")], payments=[])
        rep = self.process().json()
        saved = self.stored_invoices()
        self.assertEqual(saved[rep["missing_invoice_amount_rows"][0]]["invoice_no"], "N")
        self.assertEqual(saved[rep["unmatched_credit_note_rows"][0]]["invoice_no"], "C")


# --------------------------------------------------------------------------
# GET /invoices/reconciliation
# --------------------------------------------------------------------------
class ReconciliationTests(ApiTestCase):
    def test_basic_reconciliation(self):
        self.load([inv("A", amount=100), inv("B", amount=50, vendor="O")],
                  payments=[{"invoice_no": "A", "paid": 40}])
        r = self.client.get("/invoices/reconciliation").json()
        self.assertEqual(r["invoice_total"], 150)
        self.assertEqual(r["paid_total"], 40)
        self.assertEqual(r["balance_total"], 110)
        self.assertEqual(r["invoices"][0], {"invoice_no": "A", "invoice_amount": 100, "paid": 40, "balance": 60})

    def test_does_not_modify_files(self):
        before = (self.inv_file.read_text(), self.pay_file.read_text())
        self.client.get("/invoices/reconciliation")
        self.assertEqual((self.inv_file.read_text(), self.pay_file.read_text()), before)

    def test_excludes_cancelled_and_null_amounts(self):
        self.load([inv("A", status="CANCELLED"), inv("B", amount=None), inv("C", amount=7)], payments=[])
        r = self.client.get("/invoices/reconciliation").json()
        self.assertEqual([i["invoice_no"] for i in r["invoices"]], ["C"])

    def test_cancelled_status_is_case_insensitive(self):
        self.load([inv("A", status="cancelled"), inv("C", amount=7)], payments=[])
        r = self.client.get("/invoices/reconciliation").json()
        self.assertEqual([i["invoice_no"] for i in r["invoices"]], ["C"])

    def test_matches_process_output_after_processing(self):
        rep = self.process().json()
        rec = self.client.get("/invoices/reconciliation").json()
        self.assertEqual(rec["invoice_total"], rep["invoice_total"])
        self.assertEqual(rec["paid_total"], rep["paid_total"])
        self.assertEqual(rec["balance_total"], rep["balance_total"])
        self.assertEqual(rec["invoices"], rep["payment_reconciliation"])

    def test_reflects_new_payment_immediately(self):
        self.load([inv("A", amount=100)], payments=[])
        self.client.post("/payments", json={"invoice_no": "A", "paid": 100})
        self.assertEqual(self.client.get("/invoices/reconciliation").json()["balance_total"], 0)

    def test_paid_total_never_exceeds_payments_recorded_for_listed_invoices(self):
        # Seed data (unprocessed) has INV-101 twice; its 85,000 payment must not
        # be counted once per duplicate row.
        r = self.client.get("/invoices/reconciliation").json()
        self.assertLessEqual(r["paid_total"], 145000)

    def test_duplicate_invoice_no_does_not_double_count_payment(self):
        self.load([inv("A", amount=100), inv("A", amount=100, status="PAID")],
                  payments=[{"invoice_no": "A", "paid": 100}])
        self.assertEqual(self.client.get("/invoices/reconciliation").json()["paid_total"], 100)

    def test_process_does_not_double_count_payment_for_reused_invoice_no(self):
        # Seed: INV-102 appears twice with different amounts (not an exact dup).
        self.load(payments=[{"invoice_no": "INV-102", "paid": 1000}])
        self.assertEqual(self.process().json()["paid_total"], 1000)

    def test_malformed_payment_returns_422(self):
        self.load(payments=[{"paid": 1}])
        self.assertEqual(self.client.get("/invoices/reconciliation").status_code, 422)

    def test_missing_files_return_422(self):
        self.pay_file.unlink()
        self.assertEqual(self.client.get("/invoices/reconciliation").status_code, 422)


# --------------------------------------------------------------------------
# Cross-cutting / data integrity / robustness
# --------------------------------------------------------------------------
class DataIntegrityTests(ApiTestCase):
    def test_invoice_with_bad_date_is_rejected_on_create(self):
        r = self.client.post("/invoices", json=inv("BAD", date="not-a-date"))
        self.assertEqual(r.status_code, 422)

    def test_one_bad_invoice_must_not_break_analysis_for_all(self):
        self.client.post("/invoices", json=inv("BAD", date="not-a-date"))
        self.assertEqual(self.client.get("/invoices/analysis").status_code, 200)

    def test_duplicate_invoice_no_rejected_on_create(self):
        r = self.client.post("/invoices", json=inv("INV-104"))
        self.assertIn(r.status_code, (400, 409, 422))

    def test_update_cannot_change_invoice_no_to_an_existing_one(self):
        r = self.client.put("/invoices/INV-104", json=inv("INV-105"))
        self.assertIn(r.status_code, (400, 409, 422))

    def test_negative_payment_rejected(self):
        r = self.client.post("/payments", json={"invoice_no": "INV-101", "paid": -10})
        self.assertEqual(r.status_code, 422)

    def test_zero_payment_rejected(self):
        r = self.client.post("/payments", json={"invoice_no": "INV-101", "paid": 0})
        self.assertEqual(r.status_code, 422)

    def test_payment_for_cancelled_invoice_rejected(self):
        self.load([inv("A", status="CANCELLED")], payments=[])
        r = self.client.post("/payments", json={"invoice_no": "A", "paid": 5})
        self.assertIn(r.status_code, (400, 409, 422))

    def test_deleting_invoice_with_payments_is_blocked_or_cascades(self):
        r = self.client.delete("/invoices/INV-104")
        orphan = [p for p in self.stored_payments() if p["invoice_no"] == "INV-104"]
        invoice_gone = self.client.get("/invoices/INV-104").status_code == 404
        self.assertTrue(r.status_code == 409 or (invoice_gone and not orphan))

    def test_get_invoice_missing_data_file_is_not_500(self):
        self.inv_file.unlink()
        self.assertNotEqual(self.client.get("/invoices/INV-1").status_code, 500)

    def test_create_invoice_missing_data_file_is_not_500(self):
        self.inv_file.unlink()
        self.assertNotEqual(self.client.post("/invoices", json=inv("X")).status_code, 500)

    def test_stored_record_with_missing_field_is_not_reported_as_404(self):
        self.load([{"invoice_no": "X", "invoice_amount": 1}])
        self.assertNotEqual(self.client.get("/invoices/X").status_code, 404)

    def test_unicode_vendor_roundtrip(self):
        body = inv("U1", vendor="Café Müller 株式会社")
        self.client.post("/invoices", json=body)
        self.assertEqual(self.client.get("/invoices/U1").json()["vendor_name"], "Café Müller 株式会社")

    def test_openapi_schema_builds(self):
        self.assertEqual(self.client.get("/openapi.json").status_code, 200)

    def test_unknown_routes_and_methods(self):
        self.assertEqual(self.client.get("/nope").status_code, 404)
        self.assertEqual(self.client.patch("/invoices/INV-101").status_code, 405)
        self.assertEqual(self.client.put("/payments").status_code, 405)


if __name__ == "__main__":
    unittest.main()
