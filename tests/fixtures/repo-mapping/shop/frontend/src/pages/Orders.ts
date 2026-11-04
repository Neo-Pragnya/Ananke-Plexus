import { createOrder, getCustomer } from "@/api";

export class OrdersPage {
  submit(order: object) {
    return createOrder(order);
  }

  owner(id: number) {
    return getCustomer(id);
  }
}
