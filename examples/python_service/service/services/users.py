from ..repositories.users import read_user_record


def fetch_user_profile(user_id: int) -> dict[str, str]:
    record = read_user_record(user_id)
    return {
        "name": record["name"].title(),
        "email": record["email"],
    }

