"""Shared gunicorn settings for every microservice."""

import os
import ssl

import gunicorn

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
