"""dsh-bus MCP server — expose the file-mailbox bus to MCP-capable agents (DSH included).

Transport: stdio (default for local agents). Run:  python dshbus_mcp.py
Identity:  each caller passes ``me`` (its mailbox name); falls back to
           env DSH_BUS_NAME, then "win-mcp". Bus root comes from env
           DSH_BUS_ROOT (default ~/.dsh/windowbus) and is deliberately NOT a
           tool parameter, to keep the filesystem surface fixed.

Requires the optional ``mcp`` extra:  pip install dsh-bus[mcp]
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from mcp.server.fastmcp import FastMCP

import dshbus

mcp = FastMCP("dshbus")

_DEFAULT_ROOT = os.environ.get("DSH_BUS_ROOT") or os.path.join(
    os.path.expanduser("~"), ".dsh", "windowbus"
)


def _me(me: str | None) -> str:
    return me or os.environ.get("DSH_BUS_NAME") or "win-mcp"


@mcp.tool()
def bus_send(
    to: str,
    type: str,
    payload: dict[str, Any],
    corr: str = "",
    me: str = "",
) -> dict:
    """Send a message to another window's mailbox.

    Args:
        to: Recipient mailbox name (e.g. "window-b").
        type: Message type (e.g. "task", "notice", "result").
        payload: JSON body of the message.
        corr: Optional correlation id — the recipient should echo it in its
            ack so the sender can ``bus_wait`` on it.
        me: Sender mailbox name (defaults to DSH_BUS_NAME env, then "win-mcp").

    Returns:
        {"id": "<message id>"} — keep it for tracing; acks are keyed by corr.
    """
    bus = dshbus.Bus(_me(me), _DEFAULT_ROOT)
    mid = bus.send(to, type, payload, corr=corr)
    return {"id": mid}


@mcp.tool()
def bus_recv(
    me: str = "",
    limit: int = 10,
    timeout_s: float = 0.0,
    peek: bool = False,
) -> dict:
    """Receive (claim) messages from my mailbox.

    Args:
        me: My mailbox name (defaults to DSH_BUS_NAME env, then "win-mcp").
        limit: Max messages to claim in this call.
        timeout_s: If > 0, poll-block up to this many seconds waiting for
            the first message (AI-window callers should allow minutes, not
            seconds). 0 = non-blocking snapshot.
        peek: If true, list messages without claiming (for inspection only).

    Returns:
        List of envelopes: id, corr, type, from, payload, ts. Claimed
        messages must be finished with ``bus_ack`` or the lease expires and
        they are redelivered (at-least-once).
    """
    bus = dshbus.Bus(_me(me), _DEFAULT_ROOT)
    deadline = time.time() + timeout_s if timeout_s > 0 else 0.0
    while True:
        msgs = bus.recv(limit=limit, peek=peek)
        if msgs or not deadline or time.time() >= deadline:
            return {"count": len(msgs), "messages": msgs}
        time.sleep(dshbus.POLL_S)


@mcp.tool()
def bus_ack(mid: str, result: dict[str, Any] | None = None, me: str = "") -> dict:
    """Acknowledge a claimed message; the receipt lands in the sender's mailbox.

    Args:
        mid: Message id from ``bus_recv``.
        result: Optional result body — include the sender's ``corr`` so
            ``bus_wait`` can match it.
        me: My mailbox name.

    Returns:
        {"ok": true} on first ack; {"ok": false} if already acked.
    """
    bus = dshbus.Bus(_me(me), _DEFAULT_ROOT)
    ok = bus.ack(mid, result or {})
    return {"ok": ok}


@mcp.tool()
def bus_wait(corrs: list[str], deadline_s: float = 300.0, me: str = "") -> dict:
    """Wait for acks matching the given correlation ids.

    Args:
        corrs: Correlation ids to wait for (echoed by recipients in acks).
        deadline_s: Total wait budget in seconds (AI windows: give minutes).
        me: My (sender) mailbox name where receipts arrive.

    Returns:
        {corr: result-or-null}. Null = timed out; the receipt may still
        arrive later — check again before declaring failure.
    """
    bus = dshbus.Bus(_me(me), _DEFAULT_ROOT)
    return bus.wait(corrs, deadline_s=deadline_s)


@mcp.tool()
def bus_status() -> dict:
    """Return per-mailbox counters (inbox / claimed / acked / dead) for the bus."""
    return dshbus.Bus.status(_DEFAULT_ROOT)


@mcp.tool()
def bus_recover(ttl: float = dshbus.LEASE_TTL_S, me: str = "") -> dict:
    """Recover expired leases: redeliver stale claimed messages or dead-letter them.

    Args:
        ttl: Lease age (seconds) after which a claim counts as expired.
        me: Operator mailbox name (used for metrics only).

    Returns:
        {"redelivered": n, "dead": m}.
    """
    bus = dshbus.Bus(_me(me), _DEFAULT_ROOT)
    return bus.recover(ttl)  # {"redeliver": n, "dead": m}


def main() -> None:
    mcp.run()  # stdio


if __name__ == "__main__":
    main()
