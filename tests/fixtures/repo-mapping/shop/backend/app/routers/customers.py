from fastapi import APIRouter

from ..reports.sales import sales_by_customer
from ..services.customers import get_customer

router = APIRouter(prefix="/customers")


@router.get("/{customer_id}")
def read(customer_id: int):
    return get_customer(customer_id)


@router.get("/reports/sales")
def sales():
    return sales_by_customer()
