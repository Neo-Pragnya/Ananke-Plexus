from app.db import get_session
from app.models import Customer


def get_customer(customer_id):
    return get_session().query(Customer).get(customer_id)
