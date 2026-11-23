"""Response headers every service sends (a pure JSON API: nothing to frame, sniff or cache)."""

from flask import g

from .request_context import REQUEST_ID_HEADER

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": "default-src 'none'; frame-ancestors 'none'",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
}


def apply_security_headers(response):
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    response.headers[REQUEST_ID_HEADER] = getattr(g, "request_id", "-")
    return response
