"""Run independent blocking calls (HTTP to peer services, database reads) concurrently."""

import contextvars
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

_executor: ThreadPoolExecutor | None = None
_lock = Lock()
MAX_WORKERS = 8  # bounded: a burst can never create an unbounded number of threads


def _pool() -> ThreadPoolExecutor:
    global _executor
    with _lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=MAX_WORKERS, thread_name_prefix="ase")
        return _executor


def run_concurrently(*calls):
    """Call every zero-argument callable at the same time and return the results in order.

    Each call runs in a copy of the caller's context (request id, Flask context). The first
    exception, in argument order, is re-raised after all calls finished, so nothing is left
    running in the background.
    """
    if len(calls) == 1:
        return [calls[0]()]
    futures = [_pool().submit(contextvars.copy_context().run, call) for call in calls]
    results, failure = [], None
    for future in futures:
        try:
            results.append(future.result())
        except Exception as exc:  # noqa: BLE001 - re-raised below, after all calls finished
            failure = failure or exc
            results.append(None)
    if failure is not None:
        raise failure
    return results
