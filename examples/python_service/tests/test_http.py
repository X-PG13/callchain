from service import get_user_display


def test_get_user_display() -> None:
    assert get_user_display(1) == "Alice<alice@example.com>"

