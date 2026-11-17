#!/usr/bin/env python3
"""dsh-bus 双进程 E2E smoke（模拟两个 DSH 窗口）。

时序：
  1. 子进程 B  = smoke_consumer.py（先起，阻塞循环消费，收满 N 条逐条 ack）
  2. 父进程 A  = 本脚本，用 Bus API 连发 10 条（含中文/emoji/1MB blob）
  3. B 收满退出 → 校验 10/10 无丢失、无重复
  4. A wait(corr 全集) 收齐 10/10 回执 → PASS

用法：python -X utf8 scripts/smoke_e2e.py [bus_root]
"""
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from dshbus import Bus  # noqa: E402

PY = sys.executable
CLI = HERE / "dshbus.py"
N = 10


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else \
        Path(tempfile.mkdtemp(prefix="dshbus-smoke-"))
    print(f"[smoke] bus root = {root}")

    # ---- 1. 先起 B：阻塞消费窗口 ----
    t0 = time.time()
    proc = subprocess.Popen(
        [PY, "-X", "utf8", str(HERE / "scripts" / "smoke_consumer.py"),
         str(root), "win-b", str(N), "20"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        encoding="utf-8")
    time.sleep(1.5)   # 让 B 先进入等待（真实窗口时序：消费者先就位）

    # ---- 2. A 连发 10 条（含中文/emoji/大 blob） ----
    a = Bus("win-a", root)
    big = b"x" * (1024 * 1024)          # 1MB blob 走引用制
    mids = []
    for i in range(N):
        mid = a.send("win-b", "task",
                     {"i": i, "text": f"任务-{i} 🚀"},
                     corr=f"corr-{i}", blob=big if i == 0 else None)
        mids.append(mid)
    print(f"[smoke] A sent {N} msgs in {time.time()-t0:.2f}s")

    # ---- 3. B 收满退出 → 校验 ----
    out, err = proc.communicate(timeout=60)
    assert proc.returncode == 0, f"consumer failed: {err}"
    rep = json.loads(out)
    assert sorted(rep["ids"]) == sorted(mids), "E2E loss or duplicate!"
    assert sum(rep["has_blob"]) == 1, "blob ref missing"
    print(f"[smoke] B received {rep['n']}/{N}, blob_ref ok (consumer acked)")

    # ---- 4. A wait 收齐 ----
    res = a.wait([f"corr-{i}" for i in range(N)], deadline_s=15)
    ok = sum(1 for i in range(N) if (res or {}).get(f"corr-{i}"))
    assert ok == N, f"wait got {ok}/{N}: {res}"

    # ---- 对账 ----
    st = Bus.status(root)
    assert st["win-b"]["acked"] == N and st["win-b"]["inbox"] == 0
    print(f"[smoke] status win-b acked={st['win-b']['acked']} inbox=0")
    print("SMOKE PASS ✅  (双进程 10/10 无丢失，ack/wait 闭环, "
          f"总耗时 {time.time()-t0:.1f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
