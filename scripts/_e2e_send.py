"""E2E 窗口A发送器：向 win-b 信箱发 3 条任务（由真实 DSH 子代理会话消费）。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dshbus import Bus

ROOT = ".bus-e2e"
a = Bus("win-a", ROOT)

tasks = [
    ("stats", {"task": "stats", "desc": "统计 dshbus.py 的总行数和函数定义(def)数量"}, "e2e-stats"),
    ("guards", {"task": "guards", "desc": "列出 dshbus.py 里所有 guard_ 开头的函数名，逗号分隔"}, "e2e-guards"),
    ("unicode", {"task": "unicode", "desc": "回显token：跨窗口接力🚀DSH，并报告你的进程身份"}, "e2e-unicode"),
]
for name, payload, corr in tasks:
    mid = a.send("win-b", "task", payload, corr=corr)
    print(f"[sent] corr={corr} -> id={mid}")

print("[status]", Bus.status(ROOT))
