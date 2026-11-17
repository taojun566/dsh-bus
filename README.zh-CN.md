# dsh-bus

DSH 多窗口文件信箱总线 —— L1 文件信箱协议 v1.3 的参考实现。
单文件、纯标准库、零依赖，Windows 优先（跨平台可用）。

让同一台机器上的多个 DSH 窗口（或其他任何进程）互相**发消息、等回执、
接力任务**：一个窗口任务结束，另一个窗口自动知晓并继续。

## 快速开始

```python
from dshbus import Bus

a = Bus("window-a")                      # 我的信箱名；默认根 ~/.dsh/windowbus/
mid = a.send("window-b", "task", {"repo": "tree-forge"}, corr="job-1")
res = a.wait(["job-1"], deadline_s=60)   # 等待 window-b 的回执

# —— 窗口 B ——
b = Bus("window-b")
for m in b.recv():                    # 阻塞版: CLI recv --block
    ...                               # 处理消息
    b.ack(m["id"], {"corr": m["corr"], "ok": True})
```

```text
python dshbus.py --from window-b recv --block --timeout 60 --hb   # CLI 消费窗口
python dshbus.py --from window-a send window-b task '{"k":1}' --corr job-1
python dshbus.py --from window-a wait job-1 --deadline 60
python dshbus.py status                                          # 全信箱对账
python dshbus.py recover            # 回收过期租约（重投递 / 死信）
python dshbus.py gc                 # 清理 tmp / 过期 ack / 无引用 blob
```

## 三种消息语义（v1.3 §2）

| 语义 | 用法 | 保证 |
|---|---|---|
| 点对点 | `send("window-b", ...)` 后 B `recv()` | 独占消费，恰一接收者 |
| 公共队列 | 多个 worker 竞争同一信箱 `recv()` | link-claim 同名墓碑，恰一赢家 |
| 广播 | 循环 `send` 到每个信箱 | 每接收者一份副本 |

## 可靠性规则（实测背书，见 最大强度测试报告-20261117.md）

- **原子提交**：tmp + fsync + `os.replace`；生产者崩溃只留 tmp，不污染 committed
- **claim = `os.link` 争抢**（Windows 上 rename 有内核级双赢竞态，link 输家必得
  `FileExistsError` —— H1 修复）
- **at-least-once + 幂等契约**：消费者崩溃后租约过期由 `recover` 重投递；
  处理方必须幂等（同 id 重投递在 ack 后会被 recv 静默清理）
- **租约墓碑**：claimed/ 下 `.meta` 记录 claimant/pid/hb_ts/retries；
  TTL 30s，`heartbeat()` 续租，3 次重投递失败 → `dead/`（DLQ）
- **ack 双落点**：回执写发送方信箱（供 `wait`）+ 收件方信箱（供幂等清理）
- **blob 引用制**：大负载按 sha256 落 `blobs/`，消息只带 `blob_ref`，GC 引用计数
- **fs.watch 只是唤醒信号**：快照对账（listdir）才是真相 —— RDCW 丢事件 22.9%
  场景由轮询兜底，0 丢失

## 安全守卫（v1.3 §4）

消息名/信箱名白名单字符 + 拒路径穿越/保留设备名（CON/COM1…）/尾随点空格/
孤立代理对；目录级 reparse（junction/symlink）预置拒绝；文件级 symlink 拒绝
+ 预植硬链接（nlink>1）拒绝 + realpath 域校验。
威胁模型：单用户机器同用户进程（完整性 + 可移植性，非对抗多用户）。

## 测试

```text
python -X utf8 -m pytest tests/ -q          # 25 锁：守卫/三语义/租约/DLQ/幂等/GC/并发/CLI
python -X utf8 scripts/smoke_e2e.py         # 真实双进程 E2E（10/10 无丢失，ack/wait 闭环）
```

## DSH 窗口接入（agent 会话内直接用）

任意 DSH 窗口的 agent 用 `pwsh`/`exec` 调 CLI 即可收发，无需任何安装。

```powershell
# 窗口 A（发起方）：发任务 + 等回执
dshbus --from window-a --root <root> send window-b task '<json>' --corr job-1
dshbus --from window-a --root <root> wait job-1 --deadline 300

# 窗口 B（消费方）：循环 recv → 处理 → ack
dshbus --from window-b --root <root> recv --block --timeout 120 --limit 10
dshbus --from window-b --root <root> ack <消息id> --result '<json>'
```

实测纪律（2026-11-17 双 DSH 会话接力 E2E，见 `E2E-DSH-INTERSECTION.md`）：

- **复杂 JSON 参数走临时脚本**：pwsh 内联 JSON 会被转义撕裂；且
  PowerShell→原生程序传参会**剥掉内层双引号**（`--result '{"corr":…}'`
  到 Python 变 `{corr:…}` 直接 JSONDecodeError）。正道=写一次性
  python 辅助脚本以 argv 列表传参（或用简单扁平字符串做 result）。
- 每条命令前加 `$env:PYTHONIOENCODING='utf-8';`（emoji/中文防 GBK 崩）。
- 对 AI 窗口消费者，`wait` 的 deadline 给足（≥5 分钟：子代理启动+LLM
  推理开销远大于进程直跑）；超时返回 null ≠ 失败，回执可能后到，
  先 `status` + 查 `acks/` 再定论。
- 固定 `--from <窗口名>`，否则落到 `win-<pid>` 缺省信箱造成孤儿信箱。
- 真实活证：本仓 `.bus-e2e/` 保留现场（6 份 ack + _metrics.jsonl），
  两窗口独立报告交叉对账一致（A 侧 ack 文件 vs B 侧收尾报告）。

## 已知边界（投产前清单，不阻塞窗口间日常使用）


- 可观测性最小面（_metrics.jsonl）；背压配额/磁盘水位熔断未内置
- 大消息 >1MB 建议走 blob；超大分块未定稿
- 掉电持久性未验证（Windows 无目录 fsync）；TOCTOU 检查-使用窗口在册
- 会话生命周期与接管方判定由上层约定（协议 §6）

协议规范：`../生成产物/跨窗口通信-20261117/L1文件信箱协议规范-v1.3.md`
