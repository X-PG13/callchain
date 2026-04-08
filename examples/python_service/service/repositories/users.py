def read_user_record(user_id: int) -> dict[str, str]:
    return {
        "id": str(user_id),
        "name": "alice",
        "email": "alice@example.com",
    }

