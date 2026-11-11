from fastapi import Depends, FastAPI, Header, HTTPException, status

from .models import OrderIn, OrderOut

app = FastAPI()


def get_current_user(authorization: str = Header(...)):
    return authorization


@app.post("/orders", response_model=OrderOut, status_code=201)
def create_order(order: OrderIn, user=Depends(get_current_user)):
    if not order.items:
        raise HTTPException(status_code=422, detail="no items")
    return order


@app.get("/orders/{order_id}", response_model=OrderOut)
def read_order(order_id: int, expand: bool = False, user=Depends(get_current_user)):
    raise HTTPException(status.HTTP_404_NOT_FOUND)


@app.delete("/orders/{order_id}", status_code=204)
def delete_order(order_id: int, user=Depends(get_current_user)):
    return None


@app.get("/status")
def health() -> dict:
    return {"ok": True}
