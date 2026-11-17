# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.5.0] — 2026-11-18

### Added

- **`watch` subcommand — event-driven blocking wait, zero polling.** Wakes on
  the first filesystem event anywhere in the bus tree (message / receipt /
  claim landing) using OS-native primitives: `FindFirstChangeNotification`
  (Windows), `inotify` (Linux), `kqueue EVFILT_VNODE` (macOS/BSD) — pure
  stdlib, no new dependencies. Prints one JSON line and exits:
  `{"trigger":"bus-event"|"quiet"|"gone"|"timeout", ...}`. This is the
  wake-up primitive for orchestrators and wrapper jobs ("be notified the
  moment the other window does anything on the bus") — block in `watch`
  instead of polling.
- **Dead-man switch (`--quiet-file` + `--quiet-min`)**: optionally also exit
  when a watched file (e.g. another session's activity log) has been quiet
  for ≥ N minutes (`trigger=quiet`) or disappears (`trigger=gone`). The
  tick timer only re-evaluates that predicate and rescans directories; it
  never schedules or triggers work by itself.
- CLI now honors the `DSH_BUS_ROOT` environment variable as the default bus
  root when `--root` is not given (parity with `DSH_BUS_NAME`; previously
  `--root` was CLI-only — an asymmetry caught during live cross-window use).
- `watch` and `status` are now side-effect-free: they no longer create a
  mailbox directory as a by-product.

### Fixed

- CHANGELOG: repaired mojibake (GBK-mangled em-dashes/arrows/multiplication
  signs) in pre-1.4.2 entries.

### Tests

- 30 pytest locks (25 existing + 5 new: bus-event wake, quiet, gone,
  timeout, `DSH_BUS_ROOT` CLI fallback).

## [1.4.2] — 2026-11-17

### Fixed

- Pin MCP dependency to `mcp>=1.0,<2` — MCP SDK 2.x renamed FastMCP to MCPServer (breaking),
  which crashed `dshbus-mcp` on fresh installs. Caught by the CI matrix run #1
  (windows/ubuntu × py3.10/3.14 all-green on the core test suite) and fixed within
  the same day: this is exactly the release-then-verify loop working.

## [1.4.1] — 2026-11-17

### Fixed

- Documentation accuracy audit (every published byte re-verified): removed the
  PyPI `pip install dsh-bus` promise (no PyPI release exists) — the real
  install paths are `python dshbus.py` in place, `npx -y dsh-bus` (MCP), or
  `pip install git+https://github.com/taojun566/dsh-bus.git`.
- `pyproject.toml` declared `dshbus = "dshbus:main"` but no `main` existed —
  added `dshbus.main()`; pip-installed `dshbus` command now works.
- `pyproject.toml` stale metadata: version 1.3.0 → 1.4.1, license reference
  switched from MIT to the shipped PolyForm NC `LICENSE` file; MIT classifier
  removed.
- Removed internal release-runbook file (contained local machine paths) from
  the public repository; removed dead references to non-shipped internal
  docs; corrected an overstated "dual-OS CI matrix" claim.

## [1.4.0] — 2026-11-17

### Changed

- **License: MIT → PolyForm Noncommercial 1.0.0.** Noncommercial use
  (personal, educational, research) remains free; commercial use now requires
  a separate license from the author. Copyright line now names taojun566.
- Versions 1.3.1 and earlier were distributed under MIT and those already
  distributed copies remain under MIT (license grants are not retroactively
  revocable); all later releases are PolyForm NC.

## [1.3.0] — 2026-11-17

First public release. Reference implementation of the L1 file-mailbox bus
protocol v1.3, validated by a 14-script / 26+scenario stress campaign and a
live two-session DSH agent relay E2E.

### Core (`dshbus.py`, zero-dependency, single file)
- Atomic commit: tmp + fsync + `os.replace`; crashed producers never pollute
  committed messages.
- **`os.link` claim** — the only correct atomic-claim primitive on Windows:
  concurrent `os.rename` on the same source exhibits a kernel-level
  double-win race (both processes report success, one message on disk —
  measured 143/200 false wins). Link losers always get `FileExistsError`.
- Three delivery semantics: point-to-point (exclusive claim), shared queue
  (common tombstone dir, exactly one winner), broadcast (per-recipient copy).
- Lease tombstones: `claimed/*.meta` with claimant/pid/hb_ts/retries;
  TTL 30 s, heartbeats, 3 failed redeliveries → `dead/` (DLQ).
- ack double-write: receipt lands in the sender's mailbox (for `wait`) and
  the recipient's mailbox (for idempotent cleanup).
- at-least-once + idempotent contract; blob payloads by sha256 reference
  with reference-counting GC.
- Security guards: name whitelist, path traversal / reserved device names /
  trailing dot-space / lone surrogate rejection; directory-level reparse
  (junction/symlink) pre-placement rejection; file-level symlink rejection,
  pre-planted hard-link (nlink>1) rejection, realpath domain check.
- Cross-platform: the only OS-specific attribute access is guarded by
  `getattr(..., 0)`; core API runs on Windows and POSIX.

### CLI
- `send / recv (--block --timeout --hb) / ack / wait / status / recover / gc`
- `--from` identity, `--root` bus location, `DSH_BUS_NAME` / `DSH_BUS_ROOT`
  environment defaults.

### MCP server (`dshbus_mcp.py`, optional `[mcp]` extra)
- stdio MCP server exposing `bus_send / bus_recv / bus_ack / bus_wait /
  bus_status / bus_recover` so MCP-capable agents (DSH included) get native
  cross-window tools.

### DSH bundle (`dsh-bus-mcp/`)
- Configuration-only MCP bundle wiring the stdio server into DSH via
  `@deepseek-ai/dsh-mcp-client`.

### Validation
- pytest: 25 locks (guards / semantics / lease / DLQ / idempotency / GC /
  concurrency 8×25 zero-loss / CLI) — all green.
- Two-process smoke: 10/10 messages, ack/wait closed loop, 1 MiB blob,
  Unicode + emoji payloads.
- Two-session DSH agent relay E2E (`E2E-DSH-INTERSECTION.md`): real AI
  sessions exchanging tasks and receipts through the bus.
- Stress campaign: T1–T6 matrix (channels, concurrency, crash injection,
  fs.watch 22.9% loss quantification, security vulnerabilities found and
  fixed, junction attack reproduced and blocked).

## [1.2.0] — 2026-11-17 (internal)

- Shared tombstone directory for multi-consumer queues (per-worker tombstones
  left a double-consumption window — caught in external review).
- Wait/timeout semantics documented; `FileNotFoundError` claim path legalized.

## [1.1.0] — 2026-11-17 (internal)

- Security guards v1: name whitelist, traversal/symlink/hard-link rejection.

## [1.0.0] — 2026-11-17 (internal)

- First protocol draft: atomic commit, rename-claim (later found racy on
  Windows), three semantics, ack receipts.
