from app import add_one


def test_add_one() -> None:
    assert add_one(1) == 2
