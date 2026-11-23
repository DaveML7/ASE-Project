"""The only routes the gateway forwards: deny by default.

A request is forwarded only if (service, method, path) matches this table, so path tricks
(``..``, encoded dots, extra segments) can never reach another endpoint, in particular the
``/internal/*`` routes. Routes are grouped per service once, so a lookup only scans the
handful of routes of one service.
"""

import re
from collections import defaultdict

ID = r"[0-9]{1,18}"

# (service, allowed methods, regex that the WHOLE remaining path must match)
ROUTE_TABLE = [
    ("auth", {"POST"}, r"register|login"),
    ("auth", {"GET"}, r"me"),
    ("auth", {"POST"}, r"logout"),
    ("players", {"GET", "PATCH"}, r"me"),
    ("players", {"GET"}, r"me/monsters"),
    ("monsters", {"GET"}, rf"|{ID}|{ID}/image"),
    ("teams", {"GET", "POST"}, r""),
    ("teams", {"GET", "PUT", "DELETE"}, ID),
    ("teams", {"POST"}, rf"{ID}/validate"),
    ("encounters", {"GET", "POST"}, r""),
    ("encounters", {"GET"}, ID),
    ("history", {"GET"}, rf"|{ID}"),
]

PUBLIC = {("auth", "register"), ("auth", "login")}  # everything else needs a token...
PUBLIC_GET_SERVICES = {"monsters"}  # ...except the read-only catalogue

_BY_SERVICE: dict[str, list] = defaultdict(list)
for _service, _methods, _pattern in ROUTE_TABLE:
    _BY_SERVICE[_service].append((_methods, re.compile(_pattern)))


def match(service: str, method: str, path: str) -> tuple[bool, bool]:
    """Return (found, allowed): the path exists, and the method is allowed on it."""
    found = False
    for methods, pattern in _BY_SERVICE.get(service, ()):
        if pattern.fullmatch(path):
            found = True
            if method in methods:
                return True, True
    return found, False


def is_public(service: str, method: str, path: str) -> bool:
    return (service, path) in PUBLIC or (service in PUBLIC_GET_SERVICES and method == "GET")
