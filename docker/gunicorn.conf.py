"""Shared gunicorn settings for every microservice."""

import ssl

import gunicorn

# Do not advertise the server software/version in the `Server` header.
gunicorn.SERVER = "monster-tamers"


def ssl_context(conf, default_ssl_context_factory):
    """Accept TLS 1.3 only: every client in this system (services, browsers, curl) supports it."""
    context = default_ssl_context_factory()
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    return context
