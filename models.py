from datetime import date as Date

from pydantic import BaseModel, Field, field_validator


class Invoice(BaseModel):
    invoice_no: str = Field(min_length=1)
    invoice_amount: float | None = Field(allow_inf_nan=False)
    status: str
    vendor_name: str = "UNKNOWN"
    date: str

    @field_validator("date")
    @classmethod
    def date_must_be_iso(cls, value: str) -> str:
        try:
            Date.fromisoformat(value)
        except ValueError as error:
            raise ValueError("date must be a valid ISO date (YYYY-MM-DD)") from error
        return value


class Payment(BaseModel):
    invoice_no: str = Field(min_length=1)
    paid: float = Field(gt=0, allow_inf_nan=False)
