from .services.users import fetch_user_profile


def load_user(user_id: int) -> dict[str, str]:
    return fetch_user_profile(user_id)

