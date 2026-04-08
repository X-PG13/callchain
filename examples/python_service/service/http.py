from .handlers import load_user


def get_user_display(user_id: int) -> str:
    user = load_user(user_id)
    return f"{user['name']}<{user['email']}>"

