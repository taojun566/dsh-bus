# dsh-bus

Local file-mailbox message bus for cross-window (cross-session) agent
coordination on one machine — built and battle-tested for
[DSH](https://dsh.so) windows, usable by any process that can read files.

**中文文档: [README.zh-CN.md](README.zh-CN.md)**

## Why

Multiple AI-agent windows on the same machine cannot see each other: no
session-end events cross windows, and ad-hoc shared files race each other.
dsh-bus gives every window a durable mailbox with claim leases, receipts and
recovery — the "electrical circuit" primitives for *task finished → other
window knows*, *wait for another window*, and *relay work between windows*.

## Install

```bash
pip install dsh-bus            # core CLI (stdlib only, Python >= 3.10)
pip install "dsh-bus[mcp]"     # + MCP server for native DSH/agent integration
```

Or run straight from the repo: `python dshbus.py --help`.

## Quick start (two terminals)

```bash
# Window A: send a task, then wait for the receipt
dshbus --from window-a send window-b task '{"job":"analyze","file":"x.py"}' --corr t1
dshbus --from window-a wait t1 --deadline 600

# Window B: claim, do the work, acknowledge with the receipt
dshbus --from window-b recv
dshbus --from window-b ack <mid> --result '{"corr":"t1","status":"done"}'
```

Mailboxes live under `~/.dsh/windowbus/<name>/{inbox,claimed,acks,dead,blobs}`.

## MCP server (native DSH integration)

`dshbus-mcp` (or `python dshbus_mcp.py`) speaks MCP over stdio and exposes six
tools — `bus_send / bus_recv / bus_ack / bus_wait / bus_status / bus_recover`.
Install the configuration-only bundle in `dsh-bundle/` into your DSH profile
and **every window gains the bus tools natively** (verified: two DSH sessions
relayed a task end-to-end purely through the MCP tool surface).

## Semantics (protocol v1.3)

| Guarantee | Mechanism |
|---|---|
| Atomic delivery | write-tmp → fsync → `os.replace`; unique ids `<sender>-<ms>-<rand6>` |
| Exactly-one claimant | `os.link` to a shared tombstone name — Windows `rename` has a kernel-level double-win race (71.5% false success measured, see CHANGELOG), `link` does not |
| At-least-once + idempotency | claim lease 30 s, heartbeat, expired leases redelivered (max 3) then dead-lettered; acked ids are silently reaped |
| Receipts | `ack` writes to **both** sender and receiver mailboxes; `wait(corrs)` polls sender-side receipts |
| Poison safety | malformed JSON or symlinked/hardlinked payloads go to `dead/` with a reason |
| Hardening | path-traversal / reserved device names / reparse-point (junction) directories / dangling-surrogate guards |

Full spec: see `CHANGELOG.md` and the test suite (25 pytest locks + dual-OS CI
matrix + 8×25 concurrent-producer zero-loss lock + real stdio MCP smoke).

## Repo layout

```
dshbus.py            single-file core library + CLI (stdlib only)
dshbus_mcp.py        MCP stdio server (optional [mcp] extra)
dsh-bundle/          DSH configuration-only bundle (installs the MCP row)
tests/               pytest suite + smoke_e2e + mcp_smoke
scripts/             smoke consumer example
```

## License

MIT © 2026 dsh-bus authors

[![](https://img.shields.io/badge/powered_by-dsh-4D6BFE?style=flat-square&logo=deepseek&logoColor=white)](https://github.com/deepseek-ai/deepseek-harness)
