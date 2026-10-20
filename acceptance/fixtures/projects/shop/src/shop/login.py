from shop.users import find_user


def login(email: str, password: str) -> bool:
    user = find_user(email)
    return user is not None and user.check(password)
