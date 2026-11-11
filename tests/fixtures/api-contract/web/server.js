const express = require("express");
const app = express();

function requireAuth(req, res, next) { next(); }

app.post("/carts", requireAuth, (req, res) => {
  const { sku, quantity = 1 } = req.body;
  if (!sku) {
    return res.status(400).json({ error: "sku" });
  }
  res.status(201).json({ sku, quantity });
});

app.get("/carts/:id", (req, res) => {
  const verbose = req.query.verbose;
  res.json({ id: req.params.id, verbose });
});

module.exports = app;
