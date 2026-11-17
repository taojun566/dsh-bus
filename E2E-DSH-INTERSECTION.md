# DSH 跨窗口接力 E2E 实录（2026-11-17 21:14–21:20）

**结论：PASS** —— 两个真实 DSH AI 会话（窗口 A=主会话，窗口 B=子代理 win-b-worker）通过 dsh-bus 文件信箱完成“任务下发→处理→回执→等待”全链路，三语义（知晓/交互/等待）全部在真实窗口间成立。

## 时间线（全部为本机实测时刻）

| 时刻 | 事件 | 证据 |
|---|---|---|
| 21:14:16 | 窗口 B 发射（subagent win-b-worker，独立 DSH 会话） | subagent started |
| 21:14:43 | 窗口 A 首次发送失败（pwsh 内联 JSON 转义崩）→ 改走脚本文件 `scripts/_e2e_send.py` | 教训再证：复杂参数走脚本 |
| 21:15:07 | 窗口 A 3 条任务原子落地 win-b 信箱 | id: …-7832a7 / …-9b5199 / …-b18ece |
| 21:15:07 | 第一条被 B **实时抓走**（send 完成时 status 已见 inbox=2） | 消费者在线活证 |
| 21:19:00 | 窗口 A `wait --deadline 170` 超时返回 3×null | deadline 语义正确（见下） |
| 21:19:26–27 | 窗口 B 完成 3 条 ack，**双落点**（win-a + win-b 各 3） | ack 文件 6 份在盘 |
| 21:20:26 | 窗口 A 补查 status + 读回执：win-b inbox=0 / acked=3，内容完好 | 本文档表 2 |

## 回执内容（win-a/acks/ 原文）

| corr | B 的 answer（原文） |
|---|---|
| e2e-stats | `dshbus.py total_lines=597, def_count=27` |
| e2e-guards | `guards=guard_name,guard_dir,guard_file (3个)` |
| e2e-unicode | `token=跨窗口接力🚀DSH 已原样回显; pid=26748 (pwsh), ppid=25656; identity=DSH 窗口B subagent win-b-worker` |

三条均可独立复核：dshbus.py 行数/函数数、guard_ 函数名、Unicode+emoji 全链（中文与 🚀 无损）。

## 三语义判定

| 语义 | 判定 | 证据 |
|---|---|---|
| 任务结束被知晓 | ✅ | B 完成 → ack 双落点，A 侧 status/读回执可见，by=win-b |
| 窗口交互 | ✅ | 任务带真实工作（统计/列表/回显），B 处理并回带结果，非空转 |
| 窗口等待 | ✅ | A `wait --deadline 170` 阻塞 170s 返回 null；回执 26s 后落地，复查即得——**deadline 超时≠失败，是正确边界行为** |

## 边界发现（如实记录）

1. **wait 超时竞合**：ack 落地（21:19:26）晚于 wait 超时（21:19:00）26 秒。根因=子代理会话启动+LLM 推理延迟（B 全程 ~4.5min）大于首次 deadline。实务指引：对 AI 窗口消费者，deadline 应给足（≥5min）或超时后复查 status/acks 而非判死。
2. **信箱名缺省噪声**：现场出现 `win-14968` 空信箱（B 某条命令未带 --from，落到 pid 缺省名）。无影响；实务上 DSH 接入应固定 `--from <窗口名>`。
3. **B 的消费路径**：两轮 recv（首轮 block 抓 1 条+处理，再轮收剩余 2 条），与 README“循环 recv=真实窗口消费者”指引一致。

## 复现

```powershell
# 窗口 A（任意 DSH 会话）
python -X utf8 scripts\_e2e_send.py          # 发 3 条任务
dshbus --from window-a --root .bus-e2e wait e2e-stats e2e-guards e2e-unicode --deadline 300
# 窗口 B（另一个 DSH 会话，agent 用 pwsh 执行）
dshbus --from window-b --root .bus-e2e recv --block --timeout 120 --limit 3
#   …处理任务…
dshbus --from window-b --root .bus-e2e ack <消息id> --result '{"corr":"…","answer":"…"}'
```

现场快照曾保留于开发期 `.bus-e2e/`（含 6 份 ack 与 _metrics.jsonl）；
1.5.0 起已随仓库清理移除，本记录即为验证凭据。
