from fastapi import APIRouter

from ..services.orders import create_order, list_orders

router = APIRouter(prefix="/orders")


@router.post("")
def create(payload: dict):
    return create_order(payload["customer_id"], payload["items"])


@router.get("/{customer_id}")
def for_customer(customer_id: int):
    return list_orders(customer_id)
