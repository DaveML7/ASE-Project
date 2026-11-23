"""Abuse limits on the few endpoints worth abusing (per minute, per client or per player)."""

import os

from ase_common import RateLimiter

LIMITS = {
    "login_ip": int(os.getenv("RATE_LIMIT_LOGIN_PER_IP", "60")),
    "login_user": int(os.getenv("RATE_LIMIT_LOGIN_PER_USER", "10")),
    "register": int(os.getenv("RATE_LIMIT_REGISTER_PER_IP", "30")),
    "encounter": int(os.getenv("RATE_LIMIT_ENCOUNTERS_PER_PLAYER", "30")),
}


class Throttle:
    """Login is limited per client AND per client+username, so a distributed guess against
    one account is still slowed down; registration per client; encounters per player."""

    def __init__(self, limiter: RateLimiter | None = None, limits: dict | None = None):
        self.limiter = limiter or RateLimiter()
        self.limits = limits or LIMITS

    def _checks(self, service: str, method: str, path: str, client: str, player_id: str, body):
        route = (service, method, path)
        if route == ("auth", "POST", "login"):
            username = str(body.get("username", ""))[:64].lower() if isinstance(body, dict) else ""
            return [
                (f"login:ip:{client}", self.limits["login_ip"]),
                (f"login:user:{client}:{username}", self.limits["login_user"]),
            ]
        if route == ("auth", "POST", "register"):
            return [(f"register:{client}", self.limits["register"])]
        if route == ("encounters", "POST", ""):
            return [(f"encounter:{player_id}", self.limits["encounter"])]
        return []

    def retry_after(self, service, method, path, client, player_id, body) -> int:
        """0 if the request may go on, else the seconds to wait."""
        for key, limit in self._checks(service, method, path, client, player_id, body):
            wait = self.limiter.check(key, limit)
            if wait:
                return int(wait) + 1
        return 0
