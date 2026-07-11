import re


RESOURCE_IDENTIFIER_RE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")


def normalize_resource_identifier(value: str, label: str) -> str:
    if not RESOURCE_IDENTIFIER_RE.fullmatch(value):
        raise ValueError(
            f"{label} must be 1-128 chars, alphanumeric/underscore/dash"
        )
    return value.lower()
