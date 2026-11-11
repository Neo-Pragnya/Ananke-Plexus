---
title: Orders
status: approved
approved_by: product-owner
version: 3
---

# Orders

## Problem

Customers can't place or look up orders through the API.

## Requirements

### Placing orders

- **R1** A customer places an order with `POST /orders` and gets it back with its id.
  - An order with no items is rejected with 422.
- **R2** Placing an order needs a signed-in user.

### Looking up orders

- **R3** A customer reads one order with `GET /orders/{order_id}`.
- **R4** The order list can be filtered "by status".

## Acceptance

- R1: a valid order returns 201 and an OrderOut body.
- R2: a request without credentials returns 401.
- R3: an unknown order id returns 404.
- R4: filtering by status returns only matching orders.
