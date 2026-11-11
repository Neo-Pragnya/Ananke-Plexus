from flask import Flask, abort, jsonify, request
from flask_login import login_required

app = Flask(__name__)


@app.route("/invoices", methods=["POST"])
@login_required
def create_invoice():
    data = request.get_json()
    if "amount" not in data:
        abort(400)
    return jsonify({"id": 1, "amount": data["amount"], "currency": data.get("currency")}), 201


@app.route("/invoices/search")
def search():
    q = request.args.get("q")
    return jsonify([q])
