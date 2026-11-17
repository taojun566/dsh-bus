#!/usr/bin/env python3
"""dsh-bus 消费者窗口示例：循环 recv 直到收满 N 条或超时，逐条 ack。

这就是一个"窗口 B"的最小真实形态：阻塞消费 + 回执。
用法：python -X utf8 scripts/smoke_consumer.py <bus_root> <name> <N> <timeout_s>
输出：{"n": <收到的条数>, "ids": [...]}（stdout 单行 JSON）
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from dshbus import Bus, POLL_S  # noqa: E402


def main() -> int:
    root, name, target_n, timeout_s = (
        sys.argv[1], sys.argv[2], int(sys.argv[3]), float(sys.argv[4]))
    bus = Bus(name, root)
    deadline = time.time() + timeout_s
    got = []
    while len(got) < target_n and time.time() < deadline:
        for m in bus.recv(limit=target_n - len(got)):
            got.append(m)
            bus.ack(m["id"], {"corr": m.get("corr"), "ok": True})
        if len(got) < target_n:
            time.sleep(POLL_S)
    print(json.dumps({"n": len(got), "ids": [m["id"] for m in got],
                      "has_blob": [bool(m.get("blob_ref")) for m in got]},
                     ensure_ascii=True))
    return 0 if got else 1


if __name__ == "__main__":
    sys.exit(main())
