"""Request and response models."""

from pydantic import BaseModel, Field


class Item(BaseModel):
    sku: str
    quantity: int = 1


class OrderIn(BaseModel):
    """A new order."""

    customer_id: int
    items: list[Item]
    note: str | None = None
    priority: int = Field(..., ge=0)


class OrderOut(OrderIn):
    id: int
    status: str = "open"
