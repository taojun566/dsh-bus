#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dshbus — DSH 跨窗口文件信箱总线（L1 协议规范 v1.3 参考实现）

单文件、纯标准库。任意 DSH 窗口/终端用 python 调用即可参与跨窗口通信。

用法（CLI）：
  python dshbus.py send  <to> <type> <payload-json> [--corr C] [--from ME]
  python dshbus.py recv  [--limit N] [--block] [--timeout S] [--peek] [--from ME]
  python dshbus.py ack   <id> [--result R] [--from ME]
  python dshbus.py wait  <corr...> [--deadline S] [--from ME]
  python dshbus.py status
  python dshbus.py recover [--ttl S] [--from ME]
  python dshbus.py gc

协议（v1.3）：at-least-once 投递 + 处理方幂等（按 id 去重副作用）+ 无序投递。
claim = os.link 同名争抢（Windows rename 双赢竞态的正解，实测背书）。
租约墓碑 = claimed/<id>.msg（消息本体硬链接）+ <id>.meta{claimant,pid,lease_ts,hb_ts,retries}。
恢复判据 = hb_ts 超 TTL(30s)；重试 3 次仍令处理方崩溃 → dead/（DLQ）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import stat as statmod
import sys
import time
import unicodedata
from pathlib import Path

__version__ = "1.3.0"

# ---------------------------------------------------------------- 常量与布局

DEFAULT_ROOT = Path.home() / ".dsh" / "windowbus"

SCHEMA_VERSION = 1
LEASE_TTL_S = 30.0        # 恢复判据：hb_ts 超过该秒数视为认领者死亡
HB_INTERVAL_S = 10.0      # 认领者心跳刷新间隔
MAX_RETRIES = 3           # DLQ：同一消息重投递 3 次仍崩溃 → dead/
GC_TMP_S = 3600.0         # .tmp 残留清理阈值
GC_ACK_S = 7 * 86400.0    # acked 留痕保留窗
POLL_S = 0.1              # recv --block / wait 轮询周期

FILE_ATTRIBUTE_REPARSE_POINT = 0x400  # junction/symlink 目录标记（Windows）

# ---------------------------------------------------------------- 守卫（v1.3 §4）

_NAME_RE = re.compile(r"^[^/\\:*?\"<>|\x00-\x1f\r\n\t]{1,200}$")
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_RESERVED_CF = {s.casefold() for s in _RESERVED}   # 比较一律走 casefold 域


class GuardError(ValueError):
    """守卫拒绝——消息名/路径/目录不合法或可疑。调用方应丢弃，不得重试同名。"""


def _has_surrogate(s: str) -> bool:
    return any(0xD800 <= ord(ch) <= 0xDFFF for ch in s)


def guard_name(name: str) -> str:
    """消息 id / 信箱名守卫：白名单字符+拒穿越+保留设备名+尾随点空格+孤立代理对。"""
    if not isinstance(name, str) or not name:
        raise GuardError("name empty")
    if not _NAME_RE.match(name):
        raise GuardError(f"name has forbidden chars: {name!r}")
    if ".." in name:
        raise GuardError("name contains '..'")
    if name != name.strip(" ."):
        raise GuardError("trailing dot/space")
    if _has_surrogate(name):
        raise GuardError("lone UTF-16 surrogate")
    stem = name.split(".")[0].casefold()
    if stem in _RESERVED_CF:
        raise GuardError(f"reserved device name: {name}")
    if unicodedata.normalize("NFC", name) != name and \
       unicodedata.normalize("NFD", name) != name:
        # 仅要求规范形一致之一（跨平台文件系统互访），不强制重排
        pass
    return name


def _win_lstat(p: Path):
    try:
        return os.lstat(p)
    except OSError:
        return None


def _is_reparse(p: Path) -> bool:
    st = _win_lstat(p)
    if st is None:
        return False
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)


def guard_dir(base: Path, rel: Path) -> Path:
    """目录级守卫：rel 每个已存在路径组件都不得是 reparse point（junction/symlink）。"""
    cur = base
    for part in rel.parts:
        cur = cur / part
        if _is_reparse(cur):
            raise GuardError(f"reparse point in bus path: {cur}")
    return base / rel


def guard_file(path: Path, root: Path) -> Path:
    """文件级守卫：realpath 在 root 内 + 非 symlink + nlink==1（拒绝预植硬链接）。"""
    if path.is_symlink():
        raise GuardError(f"symlink refused: {path}")
    st = _win_lstat(path)
    if st is None:
        raise GuardError(f"missing: {path}")
    if hasattr(st, "st_nlink") and st.st_nlink > 1:
        # 注意：claim 墓碑自身就是硬链接，此守卫只用于读 inbox 原始消息与 ack
        raise GuardError(f"pre-planted hardlink refused (nlink={st.st_nlink}): {path}")
    real = Path(os.path.realpath(path))
    try:
        real.relative_to(Path(os.path.realpath(root)))
    except ValueError:
        raise GuardError(f"realpath escaped root: {real}") from None
    return path


# ---------------------------------------------------------------- 观测面（A3 最小版）

def _metrics(root: Path, rec: dict) -> None:
    try:
        with open(root / "_metrics.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=True) + "\n")
    except OSError:
        pass  # 观测失败不阻断主路径


# ---------------------------------------------------------------- 信箱布局

class Mailbox:
    def __init__(self, root: Path, name: str):
        self.root = Path(root)
        self.name = guard_name(name)
        self.base = guard_dir(self.root, Path(self.name))
        self.inbox = self.base / "inbox"
        self.claimed = self.base / "claimed"
        self.acks = self.base / "acks"
        self.dead = self.base / "dead"
        self.blobs = self.base / "blobs"
        for d in (self.inbox, self.claimed, self.acks, self.dead, self.blobs):
            d.mkdir(parents=True, exist_ok=True)
            guard_dir(self.root, d.relative_to(self.root))


# ---------------------------------------------------------------- 核心 API

class Bus:
    """一个窗口 = 一个 Bus 实例。sender 名默认取信箱名。"""

    def __init__(self, name: str = "window-a", root: Path | str | None = None):
        self.root = Path(root) if root else DEFAULT_ROOT
        guard_dir(self.root, Path("."))
        self.root.mkdir(parents=True, exist_ok=True)
        self.mb = Mailbox(self.root, name)
        self.sender = self.mb.name
        self._seq = 0

    # ---- 发送（原子提交） ----
    def send(self, to: str, type_: str, payload, corr: str | None = None,
             blob: bytes | None = None) -> str:
        """向信箱 to 原子提交一条消息。返回消息 id。blob 非 None 时走引用制。"""
        self._seq += 1
        ms = int(time.time() * 1000)
        mid = f"{self.sender}-{ms}-{secrets.token_hex(3)}"
        guard_name(mid)
        target = Mailbox(self.root, to)
        env = {
            "v": SCHEMA_VERSION, "id": mid, "seq": ms, "ts": time.time(),
            "type": type_, "corr": corr, "from": self.sender, "to": to,
            "payload": payload,
        }
        if blob is not None:
            import hashlib
            sha = hashlib.sha256(blob).hexdigest()
            bpath = target.blobs / f"{sha}.blob"
            if not bpath.exists():
                tmp = target.blobs / f"{sha}.tmp"
                with open(tmp, "wb") as f:
                    f.write(blob)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, bpath)
            env["blob_ref"] = sha
        body = json.dumps(env, ensure_ascii=True)
        tmp = target.inbox / f"{mid}.tmp"
        dst = target.inbox / f"{mid}.msg"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(body)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, dst)
        _metrics(self.root, {"ts": time.time(), "op": "send", "id": mid,
                             "from": self.sender, "to": to, "type": type_})
        return mid

    # ---- 接收（快照对账 + link-claim + 租约） ----
    def recv(self, limit: int = 10, peek: bool = False):
        """认领本信箱至多 limit 条消息（link-claim 恰一赢家，跨进程安全）。
        peek=True 只读不移除不认领。返回 envelope 列表。
        调用方处理完必须 ack(id)；崩溃恢复由 recover() 兜底（at-least-once）。"""
        out = []
        for fn in sorted(os.listdir(self.mb.inbox)):
            if len(out) >= limit:
                break
            if not fn.endswith(".msg"):
                continue
            mid = fn[:-4]
            try:
                guard_name(mid)
            except GuardError:
                self._to_dead(self.mb.inbox / fn, mid, "bad-name")
                continue
            if self._is_acked(mid):
                self._silent_unlink(self.mb.inbox / fn)  # 幂等清理
                continue
            src = self.mb.inbox / fn
            if peek:
                try:
                    guard_file(src, self.mb.base)
                    with open(src, "r", encoding="utf-8") as f:
                        out.append(json.load(f))
                except (GuardError, OSError, ValueError) as e:
                    self._to_dead(src, mid, f"peek:{e}")
                continue
            if src.is_symlink():                    # claim 前显式拒 symlink（防链接跟随）
                self._to_dead(src, mid, "symlink-src")
                continue
            env = self._claim(src, mid)
            if env is not None:
                out.append(env)
        return out

    def _claim(self, src: Path, mid: str):
        """link-claim：同名墓碑硬链接争抢。赢家移除 inbox 名并写租约 meta。
        墓碑=消息本体硬链接 → 崩溃后 payload 天然保留在 claimed/。"""
        tomb = self.mb.claimed / f"{mid}.msg"
        retries = 0
        try:
            os.link(src, tomb)          # 恰一赢家；输家 FileExistsError
        except FileExistsError:
            return None                 # 明确输给竞争者
        except FileNotFoundError:
            return None                 # 赢家已移走 src（常规竞态），本轮跳过
        # 赢家路径：继承旧 meta 的 retries（若为恢复重投递）
        oldmeta = self.mb.claimed / f"{mid}.meta"
        if oldmeta.exists():
            try:
                retries = int(json.loads(oldmeta.read_text("utf-8")).get("retries", 0))
            except (OSError, ValueError):
                retries = 0
        try:
            os.remove(src)
        except FileNotFoundError:
            pass                        # 墓碑已持有内容，安全
        self._write_meta(mid, retries)
        try:
            with open(tomb, "r", encoding="utf-8") as f:
                env = json.load(f)
            # 墓碑本身是硬链接(nlink=2)：不做 nlink 检查，只做 realpath 域校验
            real = Path(os.path.realpath(tomb))
            real.relative_to(Path(os.path.realpath(self.mb.base)))
            return env
        except (GuardError, OSError, ValueError) as e:
            self._to_dead_tomb(tomb, mid, f"poison:{e}")
            return None

    def _write_meta(self, mid: str, retries: int):
        meta = {
            "claimant": self.sender, "pid": os.getpid(),
            "lease_ts": time.time(), "hb_ts": time.time(), "retries": retries,
        }
        mpath = self.mb.claimed / f"{mid}.meta"
        tmp = self.mb.claimed / f"{mid}.meta.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, mpath)

    def heartbeat(self):
        """刷新我名下全部租约的 hb_ts（长处理任务定期调用 / recv --block 自动）。"""
        now = time.time()
        for fn in os.listdir(self.mb.claimed):
            if not fn.endswith(".meta"):
                continue
            mpath = self.mb.claimed / fn
            try:
                meta = json.loads(mpath.read_text("utf-8"))
                if meta.get("claimant") == self.sender and meta.get("pid") == os.getpid():
                    meta["hb_ts"] = now
                    tmp = mpath.with_suffix(".meta.tmp")
                    tmp.write_text(json.dumps(meta), "utf-8")
                    os.replace(tmp, mpath)
            except (OSError, ValueError):
                continue

    # ---- ack（幂等留痕·双落点） ----
    def _tomb_from(self, mid: str) -> str | None:
        """从墓碑 envelope 读消息发送方名（ack 回执方向的依据）。"""
        try:
            rec = json.loads((self.mb.claimed / f"{mid}.msg")
                             .read_text("utf-8"))
            f = rec.get("from")
            return f if isinstance(f, str) else None
        except (OSError, ValueError):
            return None

    def ack(self, mid: str, result=None):
        guard_name(mid)
        key = mid.casefold()
        # 双落点（v1.3 §5）：回执写发送方信箱（供其 wait）+ 本信箱（供幂等清理）。
        mbs = [self.mb]
        env_from = self._tomb_from(mid)
        if env_from and env_from != self.mb.name:
            mbs.append(Mailbox(self.root, env_from))
        if any((mb.acks / f"{key}.ack").exists() for mb in mbs):
            return False               # 幂等：二次 ack 无副作用
        rec = {"id": mid, "result": result, "ts": time.time(), "by": self.sender}
        for mb in mbs:
            apath = mb.acks / f"{key}.ack"
            tmp = mb.acks / f"{key}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(rec, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, apath)
        # 清墓碑
        self._silent_unlink(self.mb.claimed / f"{mid}.msg")
        self._silent_unlink(self.mb.claimed / f"{mid}.meta")
        _metrics(self.root, {"ts": time.time(), "op": "ack", "id": mid,
                             "by": self.sender})
        return True

    def _is_acked(self, mid: str) -> bool:
        return (self.mb.acks / f"{mid.casefold()}.ack").exists()

    # ---- 等待（r2-B8：deadline 语义） ----
    def wait(self, corrs, deadline_s: float = 60.0):
        """等待 corr 集对应 ack 到齐。超时返回未齐清单（None 值）。
        ack 侧将 corr 写进 result 或以 corr 作 ack 键——约定：wait 按
        acks 中 result.corr 或 corr 键匹配。"""
        deadline = time.time() + deadline_s
        want = list(corrs)
        got: dict[str, object] = {}
        while time.time() < deadline:
            for fn in os.listdir(self.mb.acks):
                if not fn.endswith(".ack"):
                    continue
                try:
                    rec = json.loads((self.mb.acks / fn).read_text("utf-8"))
                except (OSError, ValueError):
                    continue
                r = rec.get("result")
                c = None
                if isinstance(r, dict):
                    c = r.get("corr")
                got.setdefault(str(c) if c is not None else rec.get("id"), rec.get("result"))
            if all(c in got for c in want):
                return {c: got.get(c) for c in want}
            time.sleep(POLL_S)
        return {c: got.get(c) for c in want}   # 未齐项值为 None

    # ---- 恢复（租约过期 → 重投递 / DLQ） ----
    def recover(self, ttl_s: float = LEASE_TTL_S):
        """扫描本信箱租约过期墓碑：retries<MAX → 重投递（at-least-once）；
        retries>=MAX → dead/（DLQ，防跨窗口崩溃循环）。返回统计。"""
        now = time.time()
        redeliver = dead = 0
        for fn in list(os.listdir(self.mb.claimed)):
            if not fn.endswith(".meta"):
                continue
            mid = fn[:-5]
            mpath = self.mb.claimed / fn
            tomb = self.mb.claimed / f"{mid}.msg"
            try:
                meta = json.loads(mpath.read_text("utf-8"))
            except (OSError, ValueError):
                meta = {"hb_ts": 0, "retries": MAX_RETRIES}
            if now - float(meta.get("hb_ts", 0)) < ttl_s:
                continue                       # 慢而活，不动
            retries = int(meta.get("retries", 0))
            if retries + 1 >= MAX_RETRIES or not tomb.exists():
                if tomb.exists():
                    os.replace(tomb, self.mb.dead / f"{mid}.msg")
                    dead += 1
                self._silent_unlink(mpath)
            else:
                try:                           # 重投递同 id：接收方按 id 幂等
                    os.replace(tomb, self.mb.inbox / f"{mid}.msg")
                    redeliver += 1
                    # meta 保留并累加 retries，供下次认领继承（DLQ 计数跨重投递存续）
                    meta["retries"] = retries + 1
                    meta["hb_ts"] = 0.0
                    mpath.write_text(json.dumps(meta), "utf-8")
                except OSError:
                    pass
        _metrics(self.root, {"ts": now, "op": "recover", "by": self.sender,
                             "redeliver": redeliver, "dead": dead})
        return {"redeliver": redeliver, "dead": dead}

    # ---- GC（r2-B7） ----
    def gc(self, root_all: bool = True):
        """tmp 超时清理 + acked 过期清理 + blobs 引用计数清理。"""
        now = time.time()
        freed_tmp = freed_ack = freed_blob = 0
        boxes = []
        if root_all:
            for name in os.listdir(self.root):
                p = self.root / name
                if (p / "inbox").is_dir():
                    boxes.append(Mailbox(self.root, name))
        else:
            boxes.append(self.mb)
        for mb in boxes:
            for d in (mb.inbox, mb.claimed, mb.blobs, mb.acks):
                for fn in os.listdir(d):
                    if fn.endswith(".tmp") and now - _mtime(d / fn) > GC_TMP_S:
                        self._silent_unlink(d / fn)
                        freed_tmp += 1
            for fn in os.listdir(mb.acks):
                p = mb.acks / fn
                if now - _mtime(p) > GC_ACK_S:
                    self._silent_unlink(p)
                    freed_ack += 1
            # blobs 引用计数
            refs = set()
            for d in (mb.inbox, mb.claimed, mb.dead):
                for fn in os.listdir(d):
                    if not fn.endswith(".msg"):
                        continue
                    try:
                        env = json.loads((d / fn).read_text("utf-8"))
                        if env.get("blob_ref"):
                            refs.add(env["blob_ref"])
                    except (OSError, ValueError):
                        continue
            for fn in os.listdir(mb.blobs):
                if fn.endswith(".blob") and fn[:-5] not in refs:
                    self._silent_unlink(mb.blobs / fn)
                    freed_blob += 1
        return {"tmp": freed_tmp, "ack": freed_ack, "blob": freed_blob}

    # ---- 状态（A3 最小观测面） ----
    @staticmethod
    def status(root: Path | str | None = None):
        root = Path(root) if root else DEFAULT_ROOT
        rows = {}
        for name in sorted(os.listdir(root)):
            p = root / name
            if not (p / "inbox").is_dir():
                continue
            rows[name] = {
                "inbox": len([f for f in os.listdir(p / "inbox") if f.endswith(".msg")]),
                "claimed": len([f for f in os.listdir(p / "claimed") if f.endswith(".meta")]),
                "acked": len([f for f in os.listdir(p / "acks") if f.endswith(".ack")]),
                "dead": len([f for f in os.listdir(p / "dead") if f.endswith(".msg")]),
            }
        return rows

    # ---- 内部 ----
    def _to_dead(self, src: Path, mid: str, why: str):
        try:
            os.replace(src, self.mb.dead / f"{mid}.msg")
            (self.mb.dead / f"{mid}.why").write_text(why, "utf-8")
        except OSError:
            pass
        _metrics(self.root, {"ts": time.time(), "op": "dead", "id": mid, "why": why})

    def _to_dead_tomb(self, tomb: Path, mid: str, why: str):
        try:
            os.replace(tomb, self.mb.dead / f"{mid}.msg")
            (self.mb.dead / f"{mid}.why").write_text(why, "utf-8")
            self._silent_unlink(self.mb.claimed / f"{mid}.meta")
        except OSError:
            pass
        _metrics(self.root, {"ts": time.time(), "op": "dead", "id": mid, "why": why})

    @staticmethod
    def _silent_unlink(p: Path):
        try:
            os.remove(p)
        except OSError:
            pass


def _mtime(p: Path) -> float:
    try:
        return os.stat(p).st_mtime
    except OSError:
        return 0.0


# ---------------------------------------------------------------- CLI

def _cli():
    ap = argparse.ArgumentParser(prog="dshbus", description="DSH 跨窗口文件信箱总线 v" + __version__)
    ap.add_argument("--from", dest="me", default=None, help="本窗口名（默认环境 DSH_BUS_NAME 或 hostname-pid）")
    ap.add_argument("--root", default=None, help="总线根目录（默认 ~/.dsh/windowbus）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("send", help="发送消息")
    p.add_argument("to"); p.add_argument("type"); p.add_argument("payload", help="JSON 字符串")
    p.add_argument("--corr", default=None)
    p.add_argument("--blob-file", default=None, help="大消息引用文件路径")

    p = sub.add_parser("recv", help="认领消息（默认 claim；--peek 只读）")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--peek", action="store_true")
    p.add_argument("--block", action="store_true", help="阻塞直到有消息或超时")
    p.add_argument("--timeout", type=float, default=0)
    p.add_argument("--hb", action="store_true", help="长处理模式：认领后自动心跳（阻塞轮询内）")

    p = sub.add_parser("ack", help="确认处理完成")
    p.add_argument("id"); p.add_argument("--result", default=None)

    p = sub.add_parser("wait", help="等待 corr 集 ack 到齐")
    p.add_argument("corr", nargs="+")
    p.add_argument("--deadline", type=float, default=60.0)

    sub.add_parser("status", help="全部信箱状态")
    p = sub.add_parser("recover", help="扫描租约过期墓碑（重投递/DLQ）")
    p.add_argument("--ttl", type=float, default=LEASE_TTL_S)
    p = sub.add_parser("gc", help="垃圾回收")

    a = ap.parse_args()
    me = a.me or os.environ.get("DSH_BUS_NAME") or f"win-{os.getpid()}"
    bus = Bus(me, a.root)

    if a.cmd == "send":
        try:
            payload = json.loads(a.payload)
        except ValueError as e:
            sys.exit(f"payload not JSON: {e}")
        blob = None
        if a.blob_file:
            blob = Path(a.blob_file).read_bytes()
        mid = bus.send(a.to, a.type, payload, corr=a.corr, blob=blob)
        print(json.dumps({"ok": True, "id": mid}))

    elif a.cmd == "recv":
        import threading
        deadline = time.time() + (a.timeout or (86400 * 365 if a.block else 0))
        hb_stop = threading.Event()
        if a.hb:
            def _hb():
                while not hb_stop.wait(HB_INTERVAL_S):
                    bus.heartbeat()
            threading.Thread(target=_hb, daemon=True).start()
        try:
            while True:
                msgs = bus.recv(limit=a.limit, peek=a.peek)
                if msgs:
                    print(json.dumps(msgs, ensure_ascii=True))
                    return
                if time.time() >= deadline:
                    print(json.dumps([]))
                    return
                time.sleep(POLL_S)
        finally:
            hb_stop.set()

    elif a.cmd == "ack":
        ok = bus.ack(a.id, json.loads(a.result) if a.result else None)
        print(json.dumps({"ok": bool(ok)}))

    elif a.cmd == "wait":
        print(json.dumps(bus.wait(a.corr, a.deadline), ensure_ascii=True))

    elif a.cmd == "status":
        print(json.dumps(bus.status(a.root), ensure_ascii=True, indent=2))

    elif a.cmd == "recover":
        print(json.dumps(bus.recover(a.ttl)))

    elif a.cmd == "gc":
        print(json.dumps(bus.gc()))


if __name__ == "__main__":
    _cli()
