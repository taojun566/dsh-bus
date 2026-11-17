# 发布执行包（gate④ 用户确认后逐项执行，未经确认不运行任何一条）

> 状态：待确认｜生成 2026-11-17 22:36｜目标 npm 包 dsh-bus@1.3.0

## 第 0 步前置（用户操作，不可代理）

- [ ] U1. `npm adduser`（或 `npm login`）登录发布账号；`npm whoami` 非空为过
- [ ] U2. 回复确认：包名 `dsh-bus`、仓库 `taojun566/dsh-bus`(public)、MIT、v1.3.0、市场文案（见 §3 预填）

## 第 1 步 npm publish（先于一切市场动作——npx 形态依赖 registry 有包）

```powershell
# 在 D:\编程树\dsh-bus 执行（包内容=npm pack 预演 13 文件 18.8kB 已核验）
npm publish --access public
# 验证（预期 200 + version 1.3.0）：
npm view dsh-bus version
npx -y dsh-bus@latest --help   # 或快速冒烟：node launcher 链拉起
```

- 若 `dsh-bus` 名被抢注（当前 404 未占）：备选 `dshbus`（已验未占），需改 package.json name+cordis.patch.yml+README 后重走 pack 预演
- publish 后本地 bundle 验证：`plugin_manager install_bundle file:D:\编程树\dsh-bus` 应从 registry 拉 dsh-bus 成功并激活（此前失败根因即 registry 无包）

## 第 2 步 GitHub push（快照就绪 47da406，零本机 git 历史）

```powershell
cd $env:TEMP\dsh-bus-release
# 同步本轮 engines.dsh 等最新改动后再 push（快照建于 22:26，engines.dsh 22:33 落）
git add -A; git commit -m "chore: engines.dsh >=0.2.0-alpha.0 <0.3.0"
gh repo create taojun566/dsh-bus --public --source . --push
# 验证：
gh repo view taojun566/dsh-bus --json name,visibility,pushedAt
```

## 第 3 步 dshopen.org 收录申请（表单 URL 已定谳）

- 入口：https://github.com/polang233/dsh-plugin-index/issues/new?template=plugin-add.yml
- 要求核对：✅公开源码(taojun566/dsh-bus) ✅可安装 npm 版本(dsh-bus@1.3.0) ✅MIT LICENSE ✅原创 Logo(icon.svg) ✅双语描述
- 表单预填（§3）逐字粘贴，附徽章（收录后加）：
  `[![开放插件市场](https://img.shields.io/endpoint?url=https%3A%2F%2Fdshopen.org%2Fbadge%2Fdsh-bus.json)](https://dshopen.org/zh/plugins/dsh-bus/)`

## §3 表单/市场文案预填（=确认卡片内容，等用户核准）

- **npm 包名**: dsh-bus
- **仓库**: github.com/taojun566/dsh-bus (public)
- **品类**: tools（跨窗口任务投递基础设施）
- **en.json**: title "dsh-bus — Window Message Bus" / description "Cross-window file-mailbox bus: send tasks, wait for receipts, relay work between DSH sessions via six native MCP tools."
- **zh.json**: 标题 "dsh-bus — 跨窗口消息总线" / 描述 "跨窗口文件信箱总线：六大原生 MCP 工具，DSH 窗口间发任务、等回执、自动接力。"
- **engines.dsh**: >=0.2.0-alpha.0 <0.3.0（本机 0.2.1-alpha.4 实证线）
- **版本**: 1.3.0（协议 v1.3，25 pytest+双进程 smoke+双族评审全过）

## 回滚

- npm：`npm unpublish dsh-bus@1.3.0`（72h 内允许；之后需 deprecate）
- GitHub：`gh repo delete taojun566/dsh-bus --yes`（需 confirm）
- 收录：plugin-remove.yml 模板申请下架
