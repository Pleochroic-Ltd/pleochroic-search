# SPDX-License-Identifier: AGPL-3.0-or-later
"""Pleochroic: moves the residential proxy to a new exit when a search shows the current one failing.

The proxy's username carries a session id (``…-sid-<id>-…``) that pins one random residential exit, which the
provider itself rotates every hour. An exit can turn slow or get flagged well before that, and then nearly every
engine of a search fails at once: timeouts, connection errors, CAPTCHAs, 403s. :py:func:`after_search` spots that
pattern and swaps in a fresh random session id for every proxy URL that has one, so the next search leaves from
another exit.

A swap happens at most once per :py:data:`COOLDOWN` seconds per process, so concurrent failing searches do not churn
through exits, and it never cuts a request already under way: the old session's clients leave the cache at once
but are closed only after :py:data:`CLOSE_AFTER`. The id itself is part of the proxy credential and is never logged.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import string
import threading
import typing as t
from timeit import default_timer

from searx.network import client as network_client
from searx.network import network as network_module

if t.TYPE_CHECKING:
    from searx.results import ResultContainer

logger = logging.getLogger("searx.pleochroic.proxy")

COOLDOWN = 60.0
"""Seconds between two swaps in one process."""

CLOSE_AFTER = 300.0
"""Seconds an old session's clients are kept open: well past any request (``outgoing.max_request_timeout``), and
roomy for a streamed body such as ``image_proxy``'s, which has no timeout of its own."""

MIN_FAILED = 2
"""A search needs at least this many engines failing the proxy's way to count."""

FAILED_SHARE = 0.5
"""…and at least this share of the engines it asked."""

_SID = re.compile(r"(-sid-)([A-Za-z0-9]+)")
_ALPHABET = string.ascii_letters + string.digits

# Errors an exit causes: it is slow or unreachable, or the engine has flagged its address. ``handle_exception``
# records the exception's qualified class name. The HTTP client's other errors (an ``HTTPError`` for a site's own
# 404 or 503, a redirect loop) say nothing about the exit and are left out.
_EXIT_ERRORS = frozenset(
    (
        "timeout",
        "TimeoutError",
        "ssl.SSLError",
        "curl_cffi.requests.exceptions.Timeout",
        "curl_cffi.requests.exceptions.ConnectTimeout",
        "curl_cffi.requests.exceptions.ReadTimeout",
        "curl_cffi.requests.exceptions.ConnectionError",
        "curl_cffi.requests.exceptions.DNSError",
        "curl_cffi.requests.exceptions.ProxyError",
        "curl_cffi.requests.exceptions.SSLError",
        "curl_cffi.requests.exceptions.CertificateVerifyError",
        "searx.exceptions.SearxEngineCaptchaException",
        "searx.exceptions.SearxEngineAccessDeniedException",
        "searx.exceptions.SearxEngineTooManyRequestsException",
    )
)

_lock = threading.Lock()
_last_swap = -COOLDOWN
_swapping = False
_closing: set[asyncio.Future] = set()
"""Close tasks under way, held so the loop does not lose them before they finish."""


def is_exit_error(error: str) -> bool:
    """Whether an engine's ``error`` is one a bad exit causes."""
    return error in _EXIT_ERRORS


def exit_failed(engines: int, failed: int) -> bool:
    """Whether a search that asked ``engines`` engines, ``failed`` of them with an exit error, points at the exit."""
    return engines > 0 and failed >= MIN_FAILED and failed >= FAILED_SHARE * engines


def with_new_sid(url: str, sid: str) -> str:
    """``url`` with its session id replaced by ``sid``; unchanged when it has none."""
    return _SID.sub(lambda m: m.group(1) + sid, url, count=1)


def new_sid(like: str) -> str:
    """A random session id of the same length as ``like``."""
    return "".join(secrets.choice(_ALPHABET) for _ in like)


def _renewed(url: str, renewed: dict[str, str]) -> str:
    """``url`` with a fresh session id, the same one for the same URL across networks (kept in ``renewed``)."""
    if url not in renewed:
        match = _SID.search(url)
        renewed[url] = with_new_sid(url, new_sid(match.group(2))) if match else url
    return renewed[url]


def _swapped(proxies: t.Any, renewed: dict[str, str]) -> t.Any:
    if isinstance(proxies, str):
        return _renewed(proxies, renewed)
    if isinstance(proxies, list):
        return [_swapped(p, renewed) for p in proxies]
    if isinstance(proxies, dict):
        return {pattern: _swapped(p, renewed) for pattern, p in proxies.items()}
    return proxies


def swap() -> bool:
    """Gives every proxy URL with a session id a fresh one; whether there was any to swap.

    The networks' clients live on the HTTP client's event loop, so the swap runs there, between two requests.
    """
    loop = network_client.get_loop()
    if loop is None or not loop.is_running():
        return False
    return asyncio.run_coroutine_threadsafe(_swap(), loop).result(timeout=10)


async def _swap() -> bool:
    renewed: dict[str, str] = {}
    stale = []
    seen: set[int] = set()
    for net in network_module.NETWORKS.values():
        if id(net) in seen or not net.proxies:
            continue
        seen.add(id(net))
        proxies = _swapped(net.proxies, renewed)
        if proxies == net.proxies:
            continue
        net.proxies = proxies
        net._proxies_cycle = net.get_proxy_cycles()  # pylint: disable=protected-access
        # A client is cached by its proxies, so the next request builds a new one; a request still holding an old
        # one finishes on it before CLOSE_AFTER closes it.
        stale.extend(net._clients.values())  # pylint: disable=protected-access
        net._clients = {}  # pylint: disable=protected-access
    if all(old == new for old, new in renewed.items()):
        return False
    if stale:
        asyncio.get_running_loop().call_later(CLOSE_AFTER, _close_later, stale)
    _resume_exit_suspensions()
    return True


def _close_later(clients: list[t.Any]):
    task = asyncio.ensure_future(asyncio.gather(*(_close(client) for client in clients)))
    _closing.add(task)
    task.add_done_callback(_closing.discard)


async def _close(client: t.Any):
    try:
        await client.aclose()
    except Exception:  # pylint: disable=broad-except
        pass


def _resume_exit_suspensions():
    """Lifts the suspensions engines hold against the old address: the new exit has not earned them."""
    from searx.search.processors import PROCESSORS  # pylint: disable=import-outside-toplevel

    for processor in PROCESSORS.values():
        status = processor.suspended_status
        if status.is_suspended and is_exit_error(status.suspend_reason):
            status.resume()


def after_search(result_container: "ResultContainer"):
    """Swaps the exit when ``result_container``'s search failed the way a bad exit fails, cooldown permitting.

    Only engines that sent a request this time count: one skipped as already suspended says nothing new.
    """
    global _last_swap, _swapping  # pylint: disable=global-statement
    live = [e for e in result_container.unresponsive_engines if not e.suspended]
    failed = {e.engine for e in live if is_exit_error(e.error_type)}
    engines = len({timing.engine for timing in result_container.timings} | {e.engine for e in live})
    if not exit_failed(engines, len(failed)):
        return
    with _lock:
        if _swapping or default_timer() - _last_swap < COOLDOWN:
            return
        _swapping = True
    try:
        if swap():
            _last_swap = default_timer()
            logger.warning(
                "%d of %d engines failed the way a bad exit fails: moved to a new proxy session", len(failed), engines
            )
    except Exception:  # pylint: disable=broad-except
        logger.exception("could not move to a new proxy session")
    finally:
        with _lock:
            _swapping = False
