from shop.login import login


def test_unknown_user_cannot_log_in() -> None:
    assert login("nobody@example.com", "x") is False
