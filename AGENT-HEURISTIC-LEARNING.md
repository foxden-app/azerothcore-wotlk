# Agent Heuristic Learning 流程

参考：[Learning Beyond Gradients](https://trinkle23897.github.io/learning-beyond-gradients/)。

本文把论文里的 Heuristic Learning 思路落到 WoW PlayerBot Agent：不要只让 Codex 临时修一条 prompt 或规则，而是维护一个可回放、可测试、可压缩的 Heuristic System。

## 目标

当前 Agent 系统已经有事件、动作、战斗摘要、Hermes conversation、MCP typed tools 和 C++ 执行边界。新的流程要求每次迭代都留下三类证据：

- 失败或成功样本：来自 `agent_playerbot_events`、`agent_playerbot_actions` 和 `agent_playerbot_combat_summaries`。
- 可回归测试：单元测试、relay/MCP 测试、dev realm 手工验证，至少覆盖本轮改动的失败模式。
- 压缩结果：把重复补丁收敛成更简单的 typed tool、规则、prompt 约束或记忆策略，而不是无限堆说明。

## Heuristic System 组成

| 组成 | 当前落点 | 迭代时优先改哪里 |
| --- | --- | --- |
| 状态表示 | `compact_event`、MCP 查询工具、party/location/profile/combat tools | 事件缺字段或工具读错时改这里 |
| 策略/Planner | Hermes 指令、旧 `agent_bridge.py` 规则、后续 Harness Planner | 意图误解、回复风格和多步计划错时改这里 |
| Adapter | MCP typed tools、`enqueue_action`、payload schema | 参数、权限、冷却、确认、fallback 错时改这里 |
| 执行器 | `mod-playerbot-agent`、`agent_playerbot_actions` | 动作入队但 worldserver 执行错时改这里 |
| 小脑本能 | `mod-playerbots` 策略和命令 | 高频战斗/跟随/治疗行为错时改这里 |
| 反馈入口 | action result、combat summary、relay/MCP JSONL 日志、玩家反馈 | 先补可观测性，再判断是否补策略 |
| 记忆 | Hermes conversation epoch、session goal、combat summary、长期偏好表 | 旧上下文污染、跨玩家泄露、偏好写错时改这里 |
| 回放/测试 | `tools/playerbot-mcp/test_*.py`、导出的 trial、dev realm 验证 | 每个修复至少补一条 |

## 默认迭代闭环

1. 定位事件：先拿到 `event_id`，再看同一事件下的 action result。不要只凭聊天转述修 prompt。
2. 导出 trial：把当前事件、动作和附近战斗摘要保存成 JSONL。默认放在 `var/` 下，不提交运行态样本。

```bash
python3 tools/playerbot-mcp/export_hl_trial.py \
  --event-id 12345 \
  --label healer-follow-failed \
  --status failed \
  --failure-mode adapter \
  --expected "玩家要求加个奶后，瓦小狸应召唤治疗并回复失败或成功原因。" \
  --actual "动作失败后没有可见回复。" \
  --next-patch "给 summon_bot 失败增加 fallback reply。" \
  --out var/playerbot-hl-trials/trials.jsonl
```

3. 汇总趋势：把 JSONL 汇总成 CSV/JSON，先看失败模式分布，再决定下一轮改哪一层。

```bash
python3 tools/playerbot-mcp/summarize_hl_trials.py \
  var/playerbot-hl-trials/trials.jsonl \
  --csv var/playerbot-hl-trials/summary.csv \
  --json var/playerbot-hl-trials/summary.json
```

4. 分层诊断：先判断问题属于 `state_reader`、`planner`、`adapter`、`executor`、`instinct`、`memory` 还是 `infra`。一轮只优先改一个层。
5. 做最小补丁：能补 typed tool 就不要让 Hermes 拼原生命令；能补状态读取就不要把缺字段写进 prompt；能补测试就不要只靠人工复述。
6. 跑回归：至少跑受影响的 Python 单元测试。涉及 C++/world 行为时，再用 dev realm 或生产低风险事件做手工验证。
7. 回写结果：同一个 trial 的 `next_patch` 要么变成代码测试，要么变成文档约束。没有变成可执行约束的教训，下一轮还会丢。
8. 定期压缩：每 5 到 10 个 trial 或每周一次，把重复失败模式压缩成更短的规则、工具或 schema。删除过期 prompt 条款，避免指令越来越长。

## 失败模式分类

| failure_mode | 判定信号 | 典型修复 |
| --- | --- | --- |
| `state_reader` | Agent 需要的事实不存在、错字段、上下文过大或过旧 | 改 `compact_event`、MCP 查询工具、事件 meta |
| `planner` | 玩家意图理解错、多步顺序错、闲聊该回复没回复 | 改 Hermes 指令、Planner 规则、intent 测试 |
| `adapter` | intent 对了但参数、权限、冷却、fallback 错 | 改 MCP typed tool、payload 校验、action 去重 |
| `executor` | action 入队正确但 C++ 桥或 worldserver 执行错 | 改 `mod-playerbot-agent`，补执行结果 |
| `instinct` | Playerbots 本能行为不符合预期 | 改策略配置、命令映射或 mod-playerbots patch |
| `memory` | 旧 `event_id`、旧人物、跨会话/跨玩家污染 | 改 conversation epoch、session scope、记忆写入规则 |
| `infra` | 服务、端口、DB、Hermes、模型、token、白名单问题 | 改 systemd/env/部署检查，不改 Planner |

## 新能力进入标准

新增一个 Agent 能力前，必须满足：

- 有 typed intent 或 typed MCP tool；临时 raw command 只能作为过渡。
- 工具返回成功、失败和权限不足的可见结果。
- 所有回复和动作绑定 `current_event_id`。
- 随机世界 bot 仍只允许 `reply/no_reply`。
- 至少有一个单元测试覆盖 happy path 或失败 path。
- 有一条 trial 或手工验证记录说明这个能力为什么需要。

## 压缩规则

Prompt 不是长期垃圾桶。出现下面情况时要压缩：

- 同一个失败模式出现三次以上：补 typed tool 或固定规则。
- 同一个工具调用说明反复变长：把校验下沉到 MCP 或 C++。
- conversation 被旧上下文污染：旋转 epoch，并把必要事实写成短摘要或显式偏好。
- trial 中的“下一步补丁”长期没有落地：删掉或降级为 backlog，不继续塞 prompt。

## 常用命令

导出最近事件：

```bash
python3 tools/playerbot-mcp/export_hl_trial.py --latest --pretty
```

导出某个玩家最近事件：

```bash
python3 tools/playerbot-mcp/export_hl_trial.py --latest --speaker-guid 556 --pretty
```

只跑 HL trial 导出测试：

```bash
python3 -m unittest tools/playerbot-mcp/test_hl_trial_export.py
```

只跑 HL summary 测试：

```bash
python3 -m unittest tools/playerbot-mcp/test_hl_trial_summary.py
```

跑 MCP/relay 相关测试：

```bash
/home/wuya/git/azerothcore-wotlk-git/var/playerbot-mcp-venv/bin/python -m unittest \
  tools/playerbot-mcp/test_playerbot_mcp.py \
  tools/playerbot-mcp/test_hl_trial_export.py \
  tools/playerbot-mcp/test_hl_trial_summary.py
```

系统 Python 没装 `uvicorn` 时，完整 MCP 测试会在导入 `server.py` 时报错；这不代表 HL 工具失败。只测 HL 工具可用系统 Python，完整 MCP/relay 测试用上面的 runtime venv。
