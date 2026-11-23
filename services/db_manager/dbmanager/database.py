"""Connections: one thread-safe pool, and one transaction per request run as the caller's role."""

from contextlib import contextmanager
from threading import Lock

from ase_common import read_secret
from psycopg2 import pool, sql
from psycopg2.extras import RealDictCursor

from .policy import role_for

_pool: pool.ThreadedConnectionPool | None = None
_lock = Lock()


def get_pool() -> pool.ThreadedConnectionPool:
    global _pool
    with _lock:
        if _pool is None:
            # libpq reads PGHOST/PGPORT/PGDATABASE/PGUSER from the env; the password
            # comes from the secrets volume, never from an environment variable.
            _pool = pool.ThreadedConnectionPool(
                1,
                10,
                password=read_secret("POSTGRES_PASSWORD", min_length=16),
                sslmode="verify-full",
                sslrootcert="/run/app-config/ca.crt",
                connect_timeout=3,
                options="-c statement_timeout=5000 -c lock_timeout=3000",
            )
        return _pool


@contextmanager
def transaction(caller: str | None = None):
    """A transaction whose statements run with the privileges of `caller`'s own role.

    The login role of the db-manager owns no table rights at all: every query executes
    after `SET LOCAL ROLE svc_<caller>` (reset automatically when the transaction ends), so
    the database itself refuses what that service may not touch, whatever the application
    code does.
    """
    connection = get_pool().getconn()
    try:
        with connection.cursor(cursor_factory=RealDictCursor) as cursor:
            if caller is not None:
                cursor.execute(
                    sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role_for(caller)))
                )
            yield cursor
        connection.commit()
    except Exception:
        if not connection.closed:
            connection.rollback()
        raise
    finally:
        get_pool().putconn(connection)
