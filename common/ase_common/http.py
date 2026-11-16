"""One shape for every error response: {"error": <reason phrase>, "message": ...}."""

PHRASES = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    409: "Conflict",
    422: "Unprocessable Entity",
    429: "Too Many Requests",
    500: "Internal Server Error",
    502: "Bad Gateway",
    503: "Service Unavailable",
}


def error(status: int, message: str, **extra):
    """Return a (body, status) pair ready to be returned from a Flask view."""
    return {"error": PHRASES[status], "message": message, **extra}, status
