class User:
    def __init__(self, email: str, digest: str) -> None:
        self.email, self.digest = email, digest

    def check(self, password: str) -> bool:
        return hash(password) == hash(self.digest)


def find_user(email: str) -> "User | None":
    return None
