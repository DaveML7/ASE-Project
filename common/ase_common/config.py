"""Every environment-driven setting of the shared library, in one place."""

import os

CA_CERT = os.getenv("CA_CERT", "/run/app-config/ca.crt")

# Outgoing calls: a short connect timeout and a separate read timeout.
CONNECT_TIMEOUT = float(os.getenv("HTTP_CONNECT_TIMEOUT", "2"))
READ_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "10"))
HTTP_TIMEOUT = (CONNECT_TIMEOUT, READ_TIMEOUT)

# Circuit breaker.
BREAKER_FAILURES = int(os.getenv("BREAKER_FAILURES", "5"))
BREAKER_RESET = float(os.getenv("BREAKER_RESET_SECONDS", "10"))

# Reference-data cache.
CATALOGUE_TTL = float(os.getenv("CATALOGUE_TTL_SECONDS", "30"))
CATALOGUE_RETRY = 5.0  # seconds before trying again after a failed refresh

# User tokens.
JWT_TTL_SECONDS = int(os.getenv("JWT_TTL_SECONDS", "1800"))

SERVICE_URLS = {
    "auth": os.getenv("AUTH_URL", "https://auth:5001"),
    "players": os.getenv("PLAYERS_URL", "https://players:5002"),
    "monsters": os.getenv("MONSTERS_URL", "https://monsters:5003"),
    "teams": os.getenv("TEAMS_URL", "https://teams:5004"),
    "encounters": os.getenv("ENCOUNTERS_URL", "https://encounters:5005"),
    "history": os.getenv("HISTORY_URL", "https://history:5006"),
    "db-manager": os.getenv("DB_MANAGER_URL", "https://db-manager:5010"),
}
