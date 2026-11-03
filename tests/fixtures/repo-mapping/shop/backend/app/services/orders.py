"""Order logic."""
from ..db import get_session
from ..models import Order, OrderItem


def create_order(customer_id, items):
    session = get_session()
    order = Order(customer_id=customer_id)
    session.add(order)
    for sku, quantity in items:
        session.add(OrderItem(order=order, sku=sku, quantity=quantity))
    session.commit()
    return order


def list_orders(customer_id):
    return get_session().query(Order).filter(Order.customer_id == customer_id).all()


def discount(total, tier):
    if tier == "gold":
        if total > 100:
            return total * 0.8
        return total * 0.9
    if tier == "silver":
        return total * 0.95
    return total
