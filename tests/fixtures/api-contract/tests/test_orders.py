def test_create(client):
    assert client.post("/orders", json={}).status_code == 201


def test_delete_needs_auth(client):
    assert client.delete("/orders/1").status_code == 401
