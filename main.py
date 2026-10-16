from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

import services
from models import Invoice, Payment

app = FastAPI()


def _error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


@app.exception_handler(services.NotFoundError)
def handle_not_found(request: Request, error: services.NotFoundError):
    return _error(404, str(error))


@app.exception_handler(services.ConflictError)
def handle_conflict(request: Request, error: services.ConflictError):
    return _error(409, str(error))


@app.exception_handler(ValueError)
def handle_invalid_data(request: Request, error: ValueError):
    # Bad request data or bad stored data (including pydantic/JSON errors).
    return _error(422, str(error))


@app.exception_handler(OSError)
def handle_data_file_error(request: Request, error: OSError):
    return _error(422, "The data file could not be read or written.")


@app.get("/invoices/analysis")
def read_invoice_analysis():
    return services.analyze_invoices()


@app.post("/invoices/process")
def process_invoice_data():
    return services.process_invoices()


@app.get("/invoices/reconciliation")
def read_payment_reconciliation():
    return services.reconcile_payments()


@app.get("/invoices/{invoice_no}", response_model=Invoice)
def read_invoice(invoice_no: str):
    return services.get_invoice(invoice_no)


@app.post("/invoices", response_model=Invoice)
def create_new_invoice(invoice: Invoice):
    return services.create_invoice(invoice.model_dump())


@app.put("/invoices/{invoice_no}", response_model=Invoice)
def update_existing_invoice(invoice_no: str, updated_invoice: Invoice):
    return services.update_invoice(invoice_no, updated_invoice.model_dump())


@app.delete("/invoices/{invoice_no}")
def delete_existing_invoice(invoice_no: str):
    services.delete_invoice(invoice_no)
    return {"detail": "Invoice deleted successfully"}


@app.get("/payments", response_model=list[Payment])
def read_payments():
    return services.list_payments()


@app.get("/payments/by-invoice/{invoice_no}", response_model=list[Payment])
def read_payments_for_invoice(invoice_no: str):
    return services.list_payments(invoice_no)


@app.get("/payments/{payment_index}", response_model=Payment)
def read_payment(payment_index: int):
    return services.get_payment(payment_index)


@app.post("/payments", response_model=Payment, status_code=201)
def create_new_payment(payment: Payment):
    return services.create_payment(payment.model_dump())


@app.put("/payments/{payment_index}", response_model=Payment)
def update_existing_payment(payment_index: int, payment: Payment):
    return services.update_payment(payment_index, payment.model_dump())


@app.delete("/payments/{payment_index}")
def delete_existing_payment(payment_index: int):
    services.delete_payment(payment_index)
    return {"detail": "Payment deleted successfully"}
