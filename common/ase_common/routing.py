"""URL helpers shared by every service."""

from werkzeug.routing import BaseConverter, ValidationError

MAX_ROW_ID = 2**63 - 1  # the largest value of a PostgreSQL bigint key


def is_row_id(value) -> bool:
    """True for an integer that can be a primary key: 1 .. 2**63-1 (a bool is not an id)."""
    return isinstance(value, int) and not isinstance(value, bool) and 0 < value <= MAX_ROW_ID


class RowIdConverter(BaseConverter):
    """`<id:name>`: a positive 63-bit integer. Anything else (0, -1, 99999999999999999999)
    cannot name a resource, so the route simply does not match: a clean 404, never a
    database error."""

    regex = r"[0-9]{1,19}"

    def to_python(self, value: str) -> int:
        number = int(value)
        if not is_row_id(number):
            raise ValidationError()
        return number

    def to_url(self, value) -> str:
        return str(value)
