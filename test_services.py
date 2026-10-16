import unittest
from contextlib import redirect_stdout
from io import StringIO
import json
from unittest.mock import patch

import services
from services import analyze_invoice_rows, process_invoice_rows


class AnalyzeInvoiceRowsTests(unittest.TestCase):
    def test_vendor_names_are_normalized_for_groups_and_suspected_duplicates(self):
        invoices = [
            {
                "invoice_no": "INV-1",
                "vendor_name": "Acme Ltd",
                "invoice_amount": 100,
                "status": "PENDING",
                "date": "2026-01-01",
            },
            {
                "invoice_no": "INV-2",
                "vendor_name": "ACME LTD ",
                "invoice_amount": 100,
                "status": "PAID",
                "date": "2026-01-01",
            },
        ]

        analysis = analyze_invoice_rows(invoices)

        self.assertEqual(analysis["vendor_groups"], [{"vendor_names": ["Acme Ltd", "ACME LTD "]}])
        self.assertEqual(analysis["suspected_duplicates"], [{"rows": [1, 2]}])

    def test_credit_notes_cancel_only_earlier_positive_rows_once(self):
        invoices = [
            {"invoice_no": "INV-1", "vendor_name": "Acme", "invoice_amount": 100, "date": "2026-01-01"},
            {"invoice_no": "INV-2", "vendor_name": "ACME ", "invoice_amount": -100, "date": "2026-01-02"},
            {"invoice_no": "INV-3", "vendor_name": "Acme", "invoice_amount": -100, "date": "2026-01-03"},
            {"invoice_no": "INV-4", "vendor_name": "Acme", "invoice_amount": 100, "date": "2026-01-04"},
        ]

        analysis = analyze_invoice_rows(invoices)

        self.assertEqual(
            analysis["credit_notes"],
            [{"credit_note_row": 2, "canceled_invoice_row": 1}],
        )
        self.assertEqual(analysis["unmatched_credit_notes"], [{"row": 3}])

    def test_exact_duplicate_and_suspected_duplicate_are_distinct_rules(self):
        invoice = {
            "invoice_no": "INV-1",
            "vendor_name": "Acme",
            "invoice_amount": 100,
            "status": "PENDING",
            "date": "2026-01-01",
        }
        invoices = [
            invoice,
            invoice.copy(),
            {**invoice, "invoice_no": "INV-2"},
            {**invoice, "invoice_no": "INV-3", "status": "PAID"},
        ]

        analysis = analyze_invoice_rows(invoices)

        self.assertEqual(analysis["exact_duplicates"], [{"rows": [1, 2]}])
        self.assertEqual(
            analysis["suspected_duplicates"],
            [
                {"rows": [1, 3]},
                {"rows": [1, 4]},
                {"rows": [2, 3]},
                {"rows": [2, 4]},
                {"rows": [3, 4]},
            ],
        )


class ProcessInvoiceRowsTests(unittest.TestCase):
    def test_normalizes_deduplicates_cancels_and_reconciles(self):
        invoices = [
            {
                "invoice_no": "INV-101",
                "vendor_name": "  aCME ltd ",
                "invoice_amount": 200,
                "status": "pending",
                "date": "2026-01-01",
            },
            {
                "invoice_no": "INV-101",
                "vendor_name": "Acme Ltd",
                "invoice_amount": 200,
                "status": "PENDING",
                "date": "2026-01-01",
            },
            {
                "invoice_no": "INV-107",
                "vendor_name": "ACME LTD",
                "invoice_amount": 200,
                "status": "paid",
                "date": "2026-01-01",
            },
            {
                "invoice_no": "INV-201",
                "vendor_name": "Other Vendor",
                "invoice_amount": 50,
                "status": "pending",
                "date": "2026-01-01",
            },
            {
                "invoice_no": "INV-202",
                "vendor_name": "Other Vendor ",
                "invoice_amount": -50,
                "status": "pending",
                "date": "2026-01-02",
            },
            {
                "invoice_no": "INV-203",
                "vendor_name": "Other Vendor",
                "invoice_amount": -9,
                "status": "pending",
                "date": "2026-01-03",
            },
            {
                "invoice_no": "INV-106",
                "vendor_name": "Initech",
                "invoice_amount": None,
                "status": "pending",
                "date": "2026-01-04",
            },
        ]
        payments = [
            {"invoice_no": "INV-101", "paid": 80},
            {"invoice_no": "INV-101", "paid": 20},
            {"invoice_no": "INV-107", "paid": 10},
            {"invoice_no": "INV-109", "paid": 12},
        ]
        output = StringIO()

        with redirect_stdout(output):
            report = process_invoice_rows(invoices, payments)

        self.assertEqual(report["missing_invoice_amount_rows"], [5])
        json.dumps(report)
        self.assertEqual(
            list(report["exact_dupes"].values()),
            [[0, 1]],
        )
        self.assertEqual(report["removed_duplicate_rows"], [1])
        self.assertEqual(
            report["suspected_duplicates"][0]["invoice_nos"],
            ["INV-101", "INV-107"],
        )
        self.assertEqual(report["credit_note_pairs"], [(2, 3)])
        self.assertEqual(report["unmatched_credit_note_rows"], [4])
        self.assertEqual(invoices[0]["vendor_name"], "Acme Ltd")
        self.assertEqual(invoices[0]["status"], "PENDING")
        self.assertEqual(invoices[2]["status"], "CANCELLED")
        self.assertEqual(invoices[3]["status"], "CANCELLED")
        self.assertEqual(report["vendor_totals"], {"Acme Ltd": 400, "Other Vendor": -9})
        self.assertEqual(report["invoice_total"], 391)
        self.assertEqual(report["paid_total"], 110)
        self.assertEqual(report["balance_total"], 281)
        self.assertEqual(report["unmatched_payment_invoice_nos"], ["INV-109"])
        self.assertIn("INV-101 ~ INV-107", output.getvalue())
        self.assertIn("2 <-> 3", output.getvalue())
        self.assertIn("index 4 has no partner", output.getvalue())

    def test_payment_operations_validate_invoice_and_handle_missing_index(self):
        with (
            patch.object(services.repository, "find_invoice_by_no", return_value={"invoice_no": "INV-1"}),
            patch.object(services.repository, "add_payment") as add_payment,
            patch.object(services.repository, "find_payment_by_index", return_value=None),
        ):
            payment = services.create_payment({"invoice_no": "INV-1", "paid": 12})
            self.assertEqual(payment.paid, 12)
            add_payment.assert_called_once_with({"invoice_no": "INV-1", "paid": 12.0})
            with self.assertRaisesRegex(ValueError, "index 7 not found"):
                services.get_payment(7)

        with patch.object(services.repository, "find_invoice_by_no", return_value=None):
            with self.assertRaisesRegex(ValueError, "Invoice INV-404 not found"):
                services.create_payment({"invoice_no": "INV-404", "paid": 1})


if __name__ == "__main__":
    unittest.main()
