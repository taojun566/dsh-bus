# dsh-bus — cross-window task bus for DSH

Every DSH window (session) that loads this bundle gets six native tools backed by
a local file-mailbox bus (`~/.dsh/windowbus/`):

| Tool | What it does |
|---|---|
| `bus_send(to, type, payload, corr)` | Deliver a task/notice to another window's mailbox |
| `bus_recv(me, limit, timeout_s)` | Claim my inbox (blocking optional) |
| `bus_ack(mid, result)` | Finish a task; the receipt lands in the sender's mailbox |
| `bus_wait(corrs, deadline_s)` | Block until acks for my correlation ids arrive |
| `bus_status()` | Per-mailbox counters (inbox/claimed/acked/dead) |
| `bus_recover(ttl)` | Redeliver expired leases or dead-letter them |

## Typical relay between two windows

1. Window A: `bus_send(to="window-b", type="task", payload={...}, corr="t1")`
2. Window A: `bus_wait(corrs=["t1"], deadline_s=600)` — A now sleeps on the bus
3. Window B (whenever it works next): `bus_recv(me="window-b")` → does the work → `bus_ack(mid, {"corr":"t1", ...})`
4. Window A's `bus_wait` returns with B's receipt and A continues automatically.

## Semantics (protocol v1.3)

- Delivery is **at-least-once** with lease/redelivery; recipients should be idempotent on `mid`/`corr`.
- Point-to-point mailboxes are exclusively claimed; a shared queue name gives
  exactly-one-winner per message across competing consumers (`os.link` claim).
- Guards reject path traversal, reserved device names, symlinked/hardlinked
  payloads and reparse-point directories; payloads are JSON envelopes, large
  blobs are content-addressed under `blobs/`.

## Requirements

The bundle launches the MCP server through `npx -y dsh-bus@latest`, a tiny Node
launcher that spawns a Python ≥ 3.10 interpreter (probed automatically:
`DSHBUS_PYTHON` → `python3` → `python` → `py -3`; stdlib only). Alternative:
`pip install git+https://github.com/taojun566/dsh-bus.git` installs the
`dshbus` CLI and the `dshbus-mcp` console scripts.
