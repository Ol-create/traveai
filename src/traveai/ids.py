import secrets


def new_id(prefix: str) -> str:
    """Public, prefixed random ID, e.g. `del_3f9a1c...`. The prefix tells you the object type."""
    return f"{prefix}_{secrets.token_hex(12)}"
