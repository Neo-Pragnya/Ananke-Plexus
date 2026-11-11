def test_ac_r2(client):
    """AC-R2-1: no credentials."""
    assert client.post("/orders", json={}).status_code == 401
