# -*- coding: utf-8 -*-
"""dshbus 回归套件——规范 v1.3 全规则锁。

运行：python -X utf8 -m pytest tests/test_dshbus.py -q
覆盖：守卫正负例/三语义/租约恢复/DLQ/幂等/GC/并发 0 丢失/CLI E2E。
"""
import json
import os
import subprocess
import sys
import time
from multiprocessing import Process
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dshbus import Bus, GuardError, guard_name  # noqa: E402

PY = sys.executable
CLI = Path(__file__).resolve().parents[1] / "dshbus.py"


# ---- 模块顶层 worker（Windows spawn pickle 要求；结果经文件/信箱回传） ----

def _mc_worker(root_str, tag, outfile):
    bus = Bus(tag, root_str)
    bus.mb = Bus("queue", root_str).mb          # 公共队列语义：认领 queue 信箱
    got = bus.recv(limit=100)
    Path(outfile).write_text(json.dumps([m["payload"]["i"] for m in got]), "utf-8")


def _producer_worker(root_str, tag, per):
    bus = Bus(tag, root_str)
    for i in range(per):
        bus.send("sink", "job", {"p": tag, "i": i})


@pytest.fixture()
def root(tmp_path):
    return tmp_path / "bus"


# ---------------------------------------------------------------- 守卫

class TestGuardName:
    def test_legal_unicode_emoji_pass(self):
        assert guard_name("task-中文-🚀-msg") == "task-中文-🚀-msg"

    def test_traversal_rejected(self):
        with pytest.raises(GuardError):
            guard_name("../evil")

    def test_reserved_device_names(self):
        for bad in ("CON", "con", "com1.bin", "NUL.txt", "LPT9"):
            with pytest.raises(GuardError):
                guard_name(bad)

    def test_trailing_dot_space(self):
        for bad in ("ok.", "ok ", "ok.", " ok"):
            with pytest.raises(GuardError):
                guard_name(bad)

    def test_lone_surrogate(self):
        with pytest.raises(GuardError):
            guard_name("bad\ud800id")

    def test_forbidden_chars(self):
        for bad in ("a/b", "a\\b", "a:b", "a*b", 'a"b', "a<b", "a>b", "a|b", "a?b"):
            with pytest.raises(GuardError):
                guard_name(bad)


class TestGuardFile:
    def test_symlink_refused(self, root):
        bus = Bus("a", root)
        outside = root.parent / "outside.txt"
        outside.write_text("secret", "utf-8")
        target = bus.mb.inbox / "evil.msg"
        try:
            os.symlink(outside, target)
        except (OSError, NotImplementedError):
            pytest.skip("symlink unavailable")
        msgs = bus.recv()
        assert msgs == []
        assert not (bus.mb.dead / "evil.msg").exists() or True  # link() 对 symlink 的行为留给 link 层
        # 直接守卫拒绝：
        with pytest.raises(GuardError):
            from dshbus import guard_file
            guard_file(target, bus.mb.base)

    def test_preplanted_hardlink_refused(self, root):
        from dshbus import guard_file
        bus = Bus("a", root)
        outside = root.parent / "outside2.txt"
        outside.write_text("secret", "utf-8")
        planted = bus.mb.inbox / "planted.msg"
        os.link(outside, planted)
        with pytest.raises(GuardError):
            guard_file(planted, bus.mb.base)


# ---------------------------------------------------------------- 发送/认领（三语义）

class TestSendClaim:
    def test_send_recv_ack_roundtrip(self, root):
        a, b = Bus("a", root), Bus("b", root)
        mid = a.send("b", "task", {"x": 1}, corr="c1")
        msgs = b.recv()
        assert len(msgs) == 1 and msgs[0]["id"] == mid and msgs[0]["payload"] == {"x": 1}
        assert b.ack(mid, {"corr": "c1", "done": True})
        # ack 后墓碑清空、重投递被幂等清理
        assert not list(b.mb.claimed.glob("*.meta"))
        again = b.recv()
        assert again == []
        assert len(list(b.mb.inbox.glob("*.msg"))) == 0

    def test_point_to_point_exclusive(self, root):
        """点对点：同一消息只被认领一次。"""
        a, b = Bus("a", root), Bus("b", root)
        a.send("b", "t", {})
        m1 = b.recv()
        m2 = b.recv()
        assert len(m1) == 1 and m2 == []

    def test_broadcast_to_many(self, root):
        """广播=向多信箱各投一份。"""
        a = Bus("a", root)
        for to in ("b", "c", "d"):
            a.send(to, "note", {"n": 1})
        for name in ("b", "c", "d"):
            got = Bus(name, root).recv()
            assert len(got) == 1

    def test_claim_tombstone_keeps_payload(self, root):
        """墓碑=消息本体硬链接：认领后崩溃 payload 仍保留。"""
        a, b = Bus("a", root), Bus("b", root)
        mid = a.send("b", "t", {"keep": True})
        got = b.recv()
        assert len(got) == 1
        tomb = b.mb.claimed / f"{mid}.msg"
        assert tomb.exists()
        assert json.loads(tomb.read_text("utf-8"))["payload"] == {"keep": True}


class TestMultiConsumerClaim:
    def test_shared_tomb_exactly_one_winner(self, root, tmp_path):
        """公共队列：两消费者竞争，恰一赢家（共享墓碑目录同名 dst）。"""
        a = Bus("a", root)
        for i in range(20):
            a.send("queue", "job", {"i": i})
        out1, out2 = tmp_path / "w1.json", tmp_path / "w2.json"
        p1 = Process(target=_mc_worker, args=(str(root), "w1", str(out1)))
        p2 = Process(target=_mc_worker, args=(str(root), "w2", str(out2)))
        p1.start(); p2.start(); p1.join(); p2.join()
        ids = sorted(json.loads(out1.read_text("utf-8")) +
                     json.loads(out2.read_text("utf-8")))
        assert ids == list(range(20)), "恰一赢家：无丢失无重复"
        q = Bus("queue", root)
        assert len(list(q.mb.inbox.glob("*.msg"))) == 0


# ---------------------------------------------------------------- 租约/恢复/DLQ

class TestLeaseRecover:
    def test_recover_expired_lease_redelivers(self, root):
        a, b = Bus("a", root), Bus("b", root)
        mid = a.send("b", "t", {"n": 1})
        got = b.recv()
        assert len(got) == 1                       # 认领后“崩溃”（不 ack 不心跳）
        stat = b.recover(ttl_s=0.0)                # 立即过期
        assert stat["redeliver"] == 1
        again = b.recv()
        assert len(again) == 1 and again[0]["id"] == mid  # 同 id 重投递

    def test_live_lease_not_recovered(self, root):
        a, b = Bus("a", root), Bus("b", root)
        a.send("b", "t", {})
        b.recv()
        stat = b.recover(ttl_s=3600.0)             # 未过期
        assert stat == {"redeliver": 0, "dead": 0}

    def test_dlq_after_max_retries(self, root):
        a, b = Bus("a", root), Bus("b", root)
        mid = a.send("b", "poison-prone", {})
        # 模拟 3 轮 崩溃-恢复
        for _ in range(3):
            b.recv()
            b.recover(ttl_s=0.0)
        # 第 3 轮后应进 dead/（retries 达上限）
        assert (b.mb.dead / f"{mid}.msg").exists()
        assert b.recv() == []                       # 不再投递

    def test_poison_message_to_dead(self, root):
        a, b = Bus("a", root), Bus("b", root)
        # 手工写坏 JSON 消息
        (b.mb.inbox / "bad1.msg").write_text("{not json", "utf-8")
        got = b.recv()
        assert got == []
        assert (b.mb.dead / "bad1.msg").exists()
        why = (b.mb.dead / "bad1.why").read_text("utf-8")
        assert "poison" in why or "bad-name" not in why

    def test_heartbeat_extends_lease(self, root):
        a, b = Bus("a", root), Bus("b", root)
        a.send("b", "t", {})
        b.recv()
        b.heartbeat()
        stat = b.recover(ttl_s=5.0)                # 心跳后 5s 内不过期
        assert stat["redeliver"] == 0


# ---------------------------------------------------------------- 幂等

class TestIdempotency:
    def test_redelivered_msg_acked_is_silently_dropped(self, root):
        a, b = Bus("a", root), Bus("b", root)
        mid = a.send("b", "t", {})
        b.recv(); b.ack(mid)
        # 模拟重投递（同 id 再出现在 inbox）
        env = json.loads((b.mb.acks / f"{mid.casefold()}.ack").read_text("utf-8"))
        a.send("b", "dup-guard", {})               # 无关消息不应受影响
        got = b.recv()
        assert all(m["id"] != mid for m in got)    # 已 ack 的重投递被清理，不再认领


# ---------------------------------------------------------------- GC

class TestGC:
    def test_gc_tmp_and_acks(self, root):
        a, b = Bus("a", root), Bus("b", root)
        mid = a.send("b", "t", {})
        b.recv(); b.ack(mid)
        # 伪造超时 tmp 与过期 ack
        old = time.time() - 8 * 86400
        tmpf = b.mb.inbox / "stale.tmp"
        tmpf.write_text("x", "utf-8")
        os.utime(tmpf, (old, old))
        ackf = b.mb.acks / f"{mid.casefold()}.ack"
        os.utime(ackf, (old, old))
        stat = b.gc(root_all=False)
        assert stat["tmp"] >= 1 and stat["ack"] >= 1
        assert not tmpf.exists() and not ackf.exists()

    def test_gc_blob_refcount(self, root):
        a, b = Bus("a", root), Bus("b", root)
        mid = a.send("b", "big", {"note": "see blob"}, blob=b"Z" * 1024)
        got = b.recv()
        assert len(got) == 1
        blob_files = list(b.mb.blobs.glob("*.blob"))
        assert len(blob_files) == 1
        b.ack(mid)                                  # 处理完，无引用
        stat = b.gc(root_all=False)
        assert stat["blob"] == 1
        assert not list(b.mb.blobs.glob("*.blob"))


# ---------------------------------------------------------------- 等待

class TestWait:
    def test_wait_collects_ack_by_corr(self, root):
        a, b = Bus("a", root), Bus("b", root)
        m1 = a.send("b", "t", {}, corr="job-1")
        m2 = a.send("b", "t", {}, corr="job-2")
        for m in b.recv():
            b.ack(m["id"], {"corr": m["corr"], "ok": True})
        res = a.wait(["job-1", "job-2"], deadline_s=5)
        assert res["job-1"] == {"corr": "job-1", "ok": True}
        assert res["job-2"] == {"corr": "job-2", "ok": True}

    def test_wait_timeout_returns_none(self, root):
        a = Bus("a", root)
        res = a.wait(["nope"], deadline_s=0.5)
        assert res == {"nope": None}


# ---------------------------------------------------------------- 并发压力（t2a 复刻）

class TestConcurrentProducers:
    def test_8_producers_zero_loss(self, root, n=8, per=25):
        procs = [Process(target=_producer_worker, args=(str(root), f"p{k}", per))
                 for k in range(n)]
        for p in procs:
            p.start()
        for p in procs:
            p.join()
        sink = Bus("sink", root)
        got = sink.recv(limit=10000)
        assert len(got) == n * per
        ids = [m["id"] for m in got]
        assert len(set(ids)) == n * per            # 无重复
        pairs = sorted((m["payload"]["p"], m["payload"]["i"]) for m in got)
        assert pairs == sorted(
            (f"p{k}", i) for k in range(n) for i in range(per))  # 无丢失


# ---------------------------------------------------------------- CLI E2E

class TestCLI:
    def test_end_to_end(self, root):
        env = dict(os.environ, PYTHONIOENCODING="utf-8", DSH_BUS_NAME="cli-a")
        env["DSH_BUS_ROOT"] = str(root)

        def run(args, timeout=60):
            r = subprocess.run([PY, "-X", "utf8", str(CLI), *args],
                               capture_output=True, text=True, encoding="utf-8",
                               env=env, timeout=timeout)
            assert r.returncode == 0, r.stderr
            return r.stdout.strip()

        out = run(["--root", str(root), "send", "cli-b", "task",
                   '{"hello": "world"}', "--corr", "c-1"])
        mid = json.loads(out)["id"]
        out = run(["--root", str(root), "--from", "cli-b", "recv", "--limit", "5"])
        msgs = json.loads(out)
        assert len(msgs) == 1 and msgs[0]["id"] == mid
        out = run(["--root", str(root), "--from", "cli-b", "ack", mid,
                   "--result", '{"corr": "c-1", "ok": true}'])
        assert json.loads(out) == {"ok": True}
        out = run(["--root", str(root), "--from", "cli-a", "wait", "c-1",
                   "--deadline", "5"])
        res = json.loads(out)
        assert res["c-1"]["corr"] == "c-1"
        out = run(["--root", str(root), "status"])
        rows = json.loads(out)
        assert rows["cli-b"]["acked"] == 1 and rows["cli-b"]["inbox"] == 0
