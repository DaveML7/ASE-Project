"""Shared gunicorn settings for every microservice."""

import json
import os
import ssl

import gunicorn
import gunicorn.util

# Do not advertise the server software/version in the `Server` header.
gunicorn.SERVER = "monster-tamers"

# Hung or very slow requests are cut instead of holding a worker forever.
timeout = int(os.getenv("GUNICORN_TIMEOUT", "30"))
graceful_timeout = 20
keepalive = 5

# The control socket is an extra management surface that nothing here needs.
control_socket_disable = True

# Recycle each worker after a number of requests (with jitter so they do not restart
# together): a slow leak can never grow without bound. The gateway sets MAX_REQUESTS=0
# because it keeps the rate-limit counters in memory and must not lose them.
max_requests = int(os.getenv("MAX_REQUESTS", "2000"))
max_requests_jitter = max_requests // 10

# Heartbeat files on a RAM disk: no disk I/O on the request path.
worker_tmp_dir = "/dev/shm"  # nosec B108 - private tmpfs inside the container

# One line per request with the id that ties it to the other services' logs.
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(L)ss rid=%({x-request-id}o)s'


def ssl_context(conf, default_ssl_context_factory):
    """Accept TLS 1.3 only: every client in this system (services, browsers, curl) supports it."""
    context = default_ssl_context_factory()
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    return context


def _json_error(sock, status_int, reason, mesg):
    """Requests refused by gunicorn itself (URI or headers too large, broken HTTP, bad TLS)
    never reach Flask, so they would be an HTML page: answer in the API's JSON shape instead.
    The parser's own message is not echoed back (it can quote the client's input)."""
    body = json.dumps(
        {"error": reason, "message": "the request was refused before it reached the service"}
    ).encode()
    head = (
        f"HTTP/1.1 {status_int} {reason}\r\nContent-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\nCache-Control: no-store\r\n"
        "X-Content-Type-Options: nosniff\r\nConnection: close\r\n\r\n"
    )
    try:
        sock.sendall(head.encode("latin-1") + body)
    except OSError:
        pass  # the client already went away


gunicorn.util.write_error = _json_error
