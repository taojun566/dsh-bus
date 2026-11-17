"""watch 子命令（事件驱动阻塞等待）与 DSH_BUS_ROOT 环境变量行为测试。

watch 的触发类型：bus-event / quiet / gone / timeout（详见 dshbus.cli_watch）。
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PKG = HERE.parent / "dshbus.py"


def _spawn_watch(root, extra=()):
    return subprocess.Popen(
        [sys.executable, str(PKG), "--root", str(root), "watch", *map(str, extra)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def test_watch_bus_event(tmp_path):
    """总线树出现文件事件 → 立即以 trigger=bus-event 退出（不等满 timeout）。"""
    root = tmp_path / "bus"
    proc = _spawn_watch(root, ["--timeout", "20"])
    try:
        time.sleep(2.0)                      # 等监听就位（进程启动+注册）
        mb = root / "w1" / "inbox"
        mb.mkdir(parents=True)
        (mb / "m1.msg").write_text("{}", encoding="utf-8")
        out, err = proc.communicate(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()
    assert proc.returncode == 0, err
    rec = json.loads(out)
    assert rec["trigger"] == "bus-event"
    assert rec["elapsed"] < 20               # 是事件唤醒，不是超时兜底


def test_watch_quiet(tmp_path):
    """quiet-file 已静默超阈值 → 静默隙内以 trigger=quiet 退出。"""
    root = tmp_path / "bus"
    root.mkdir()
    qf = tmp_path / "work.log"
    qf.write_text("x", encoding="utf-8")
    old = time.time() - 120
    os.utime(qf, (old, old))                 # 已静默 2 分钟 ≥ 1 分钟阈值
    proc = _spawn_watch(root, ["--timeout", "20",
                               "--quiet-file", str(qf), "--quiet-min", "1"])
    out, err = proc.communicate(timeout=15)
    assert proc.returncode == 0, err
    assert json.loads(out)["trigger"] == "quiet"


def test_watch_gone(tmp_path):
    """quiet-file 消失（进程终止/被移走）→ trigger=gone。"""
    root = tmp_path / "bus"
    root.mkdir()
    qf = tmp_path / "gone.log"
    qf.write_text("x", encoding="utf-8")
    proc = _spawn_watch(root, ["--timeout", "20",
                               "--quiet-file", str(qf), "--quiet-min", "1"])
    time.sleep(2.0)
    qf.unlink()
    out, err = proc.communicate(timeout=15)
    assert proc.returncode == 0, err
    assert json.loads(out)["trigger"] == "gone"


def test_watch_timeout(tmp_path):
    """无事件且无判据 → 到 timeout 以 trigger=timeout 退出。"""
    root = tmp_path / "bus"
    root.mkdir()
    proc = _spawn_watch(root, ["--timeout", "1"])
    out, err = proc.communicate(timeout=10)
    assert proc.returncode == 0, err
    assert json.loads(out)["trigger"] == "timeout"


def test_cli_root_env(tmp_path):
    """DSH_BUS_ROOT 环境变量生效：CLI 无 --root 时落在 env 指定根。"""
    root2 = tmp_path / "envbus"
    env = dict(os.environ, DSH_BUS_ROOT=str(root2))
    r = subprocess.run(
        [sys.executable, str(PKG), "--from", "t", "send", "u", "note", "{}"],
        env=env, capture_output=True, text=True, timeout=10)
    assert r.returncode == 0, r.stderr
    assert json.loads(r.stdout)["ok"] is True
    assert (root2 / "u" / "inbox").is_dir()
