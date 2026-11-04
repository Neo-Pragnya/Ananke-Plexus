import { http } from "./lib/http";

export async function createOrder(order: object) {
  const response = await fetch("/api/orders", { method: "POST", body: JSON.stringify(order) });
  return response.json();
}

export function getCustomer(id: number) {
  return http.get(`/customers/${id}`);
}

export function legacyInvoices() {
  return fetch("/api/invoices");
}
