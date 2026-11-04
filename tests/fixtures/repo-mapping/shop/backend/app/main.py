from fastapi import FastAPI

from .routers import customers, orders

app = FastAPI()
app.include_router(orders.router, prefix="/api")
app.include_router(customers.router, prefix="/api")


@app.get("/health")
def health():
    return {"ok": True}
