"""Flask application factory shared by every microservice."""

from flask import Flask, request

from .handlers import register_error_handlers
from .http import error
from .keys import jwt_private_key, jwt_public_key, service_private_key
from .request_context import assign_request_id
from .secrets import read_secret
from .security import apply_security_headers

LOOPBACK = {"127.0.0.1", "::1"}
KEY_LOADERS = {
    "service": service_private_key,
    "jwt_public": jwt_public_key,
    "jwt_private": jwt_private_key,
}


def create_app(
    name: str,
    required_secrets: tuple[str, ...] = (),
    required_keys: tuple[str, ...] = ("service", "jwt_public"),
    public_health: bool = False,
) -> Flask:
    """Build a service app. Fails fast if a required secret or key is missing.

    `/health` answers only to the container's own healthcheck (loopback) unless
    `public_health` is set (the gateway): other containers cannot probe services.
    """
    for secret in required_secrets:
        read_secret(secret)
    for key in required_keys:
        KEY_LOADERS[key]()

    app = Flask(name)
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
    app.json.sort_keys = False

    @app.get("/health")
    def health():
        if not public_health and request.remote_addr not in LOOPBACK:
            return error(404, "not found")
        return {"status": "ok"}

    register_error_handlers(app)
    app.before_request(assign_request_id)
    app.after_request(apply_security_headers)
    return app
