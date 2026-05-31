# Agents Studio AI · 分阶段开发计划

> 核心理念：**先把 Skill 跑通，再用最少的代码长出后端，最后再加 UI。**
>
> 这条路径与 ArcReel / Toonflow / huobao-drama 等领先项目的实际演化路径一致 —— 它们都是先把"流程对话能跑通"做到 70%，再补 UI 与多用户支持。

---

## 全景路线图

```
┌─────────────┬─────────────┬─────────────┬─────────────┬─────────────┐
│   M0        │    M1       │    M2       │    M3       │    M4       │
│  (Week 1)   │ (Week 2-3)  │ (Week 4-6)  │ (Week 7-9)  │  (Week 10+) │
│             │             │             │             │             │
│  Skill      │  CLI MVP    │  Web MVP    │  Studio     │  Ecosystem  │
│  原型       │  端到端     │  可视化     │  完整版     │  集成扩展   │
│             │             │             │             │             │
│  Markdown   │  +Adapter   │  +FastAPI   │  +SessionAc │  +ComfyUI   │
│  Skills     │  +Queue     │  +React UI  │  +Sandbox   │  +MCP Tool  │
│             │  +CLI       │  +SSE       │  +Versions  │  +OpenClaw  │
└─────────────┴─────────────┴─────────────┴─────────────┴─────────────┘
```

---

## M0 · Skill 原型（Week 1）— 本仓库当前阶段

> **目标**：不写一行后端代码，用任何支持 Markdown SKILL 协议的 Agent（Claude Agent SDK / Cursor / Cline / Kiro）就能把"小说 → 短剧成片"流程跑通 70%。

### 交付物
- ✅ `docs/INSIGHTS.md` — 9 仓库洞察分析
- ✅ `docs/ARCHITECTURE.md` — 平台架构蓝图
- ✅ `docs/ROADMAP.md` — 本文档
- ✅ `skills/00-orchestrator/SKILL.md` — 编排 Skill（13 步状态机）
- ✅ `skills/01-novel-analyst/SKILL.md` — 全本理解
- ✅ `skills/02-show-planner/SKILL.md` — 7 参数协商
- ✅ `skills/03-script-writer/SKILL.md` — 一次性出全集剧本
- ✅ `skills/04-asset-extractor/SKILL.md` — 全集资产提取
- ✅ `skills/05-art-director/SKILL.md` — 画风定调
- ✅ `skills/06-character-designer/SKILL.md` — 角色一致性设计
- ✅ `skills/07-storyboard-breaker/SKILL.md` — 分镜拆解
- ✅ `skills/08-keyframe-generator/SKILL.md` — 关键帧生成
- ✅ `skills/09-video-generator/SKILL.md` — 视频片段生成
- ✅ `skills/10-voice-assigner/SKILL.md` — 角色音色分配
- ✅ `skills/11-tts-synthesizer/SKILL.md` — TTS 配音
- ✅ `skills/12-video-composer/SKILL.md` — 拼接成片
- ✅ `art-styles/2D-chinese-anime/` — 一个完整的画风包样例
- ✅ `packages/adapters/types.ts` — Provider Adapter 接口定义（仅类型，无实现）
- ✅ `packages/asset-spec/character.yaml` — 资产 Spec 样例

### 验证方式
1. 把整个仓库 clone 到本地
2. 在 Cursor / Claude Code / Kiro 中加载 `skills/` 目录
3. 跟 Agent 对话："我有一篇小说想做一集 2 分钟短剧" → 看 Agent 是否按 Orchestrator 的状态机走完整个流程
4. **不要求每一步都真正调用 API**（M0 阶段允许 Agent 用占位输出），但要求 **流程编排正确、状态机切换正确、Skill 之间衔接正确**

### 验收 DoD
- [ ] Orchestrator 能从空白项目状态开始，依次 dispatch 13 个 Skill（含 M1 内容关卡 / M2 资产关卡 / M3 单集循环）
- [ ] 每个 Subagent 完成后产出对应的结构化数据（即使是占位）
- [ ] 用户可以从任意阶段进入（"我已经有剧本了，直接做第 1 集"）
- [ ] 所有 Skill 都有 YAML frontmatter 且 description 描述准确
- [ ] M1 内容关卡的串行依赖正确（01 不出 02 拒绝跑、02 plan 未 locked 03 拒绝跑）

---

## M1 · CLI MVP（Week 2-3）

> **目标**：把 Skill 真正接通模型调用，用 CLI 跑通端到端，输出第 1 个 Demo 短剧。
>
> **当前状态（已交付：离线 mock 骨架 + 真实 HTTP Adapter）** 🚧
> `src/` 下已落地一个**纯标准库、可离线运行**的 CLI 运行时：Orchestrator 状态机已写成代码（`core/state_machine.py`，忠实映射 `skills/00-orchestrator/SKILL.md` 路由表），14 个 Skill 的执行器端到端产出全套落盘产物，CLI 提供 `new / status / next / run / compose / skills / providers`。
> **真实供应商已接入**：`backends/` 内置 OpenAI 兼容（文本/图像）、Seedream（图像）、Seedance（视频）、MiniMax（配音 T2A v2 + Hailuo 视频）Adapter，采用 `build_request`/`parse_response` 分离设计，可用 `FakeTransport` 做**完整离线单测**；运行时按环境变量解析供应商，无凭证自动回退 mock。
> **剩余 M1 工作**：把内存顺序执行器升级为 lease-based `GenerationQueue`，以及对接真实 Key 的线上联调验证。

### 已交付的实际目录（mock 骨架）

```
agents-studio-ai/
  pyproject.toml              # 纯标准库；console_scripts: studio = cli.main:main
  examples/sample_novel.txt   # 端到端冒烟用示例小说
  src/
    cli/
      main.py                 # argparse 入口：new/status/next/run/compose/skills/providers
      __main__.py             # python -m cli
    core/
      project.py              # ✅ ProjectManager（project.json 单一真相）
      state_machine.py        # ✅ Orchestrator 状态机（路由表代码化）
      constants.py            # ✅ readiness 状态机 + weight tier + skill id
      miniyaml.py             # ✅ 零依赖 YAML 输出器（production_plan.yaml 等镜像）
    backends/
      base.py                 # ✅ Image/Video/Text/TTS Adapter 协议 + ProviderRequest/Transport
      http.py                 # ✅ urllib 传输 + FakeTransport（离线单测）+ HTTP Adapter 基类
      mock.py                 # ✅ mock Adapter（离线、确定性、带成本估算）
      openai_compat.py        # ✅ OpenAI 兼容（chat + images/generations）
      volcengine.py           # ✅ Seedream 图像（同步）+ Seedance 视频（异步任务/轮询）
      minimax.py              # ✅ MiniMax T2A v2 配音（hex 音频）+ Hailuo 视频
      config.py               # ✅ 环境变量解析（显式>env>默认，无凭证回退 mock）
      registry.py             # ✅ 注册表 + 解析兜底（仿 huobao registry.ts）
    agent_runtime/
      skill_loader.py         # ✅ 读 skills/<id>/SKILL.md frontmatter
      executors.py            # ✅ 01-12 各 Skill 的 mock 执行器
      runner.py               # ✅ 不依赖完整 Agent SDK 的最小 runner（含成本关卡）
  skills/                     # 已在 M0 完成
  art-styles/                 # 已在 M0 完成
  packages/                   # 已在 M0 完成
```

### 增量交付物（计划态 · 真实 Adapter + 队列）

```
agents-studio-ai/
  pyproject.toml              # uv 项目
  src/
    cli/                      # ✨ 新增
      __init__.py
      main.py                 # `studio` 命令入口
      commands/
        new.py                # studio new <name>
        run.py                # studio run --skill <id>
        compose.py            # studio compose --episode 1
    core/
      project.py              # ProjectManager（仿 ArcReel）
      asset_spec.py           # 资产 Spec 加载
      script_models.py        # Pydantic：DramaScene / NarrationSegment
      generation_queue.py     # Lease-based 队列（先单进程内存版）
      version_manager.py
    backends/                 # ✨ Adapter 实现
      image/
        base.py
        openai.py
        gemini.py
        volcengine.py        # Seedream
        ali.py                # Wanx
      video/
        base.py
        volcengine.py         # Seedance
        ali.py                # Wan
        vidu.py
      text/
        base.py
        openai_compat.py     # 兼容 GPT/Qwen/DeepSeek/Claude
        gemini.py
      tts/
        base.py
        minimax.py
        edge.py
    agent_runtime/            # ✨ Skill Runner
      skill_loader.py         # 读 skills/<id>/SKILL.md
      tool_registry.py        # MCP Tool 实现
      runner.py               # 不依赖完整 Agent SDK 的最小 runner
  skills/                     # 已在 M0 完成
  art-styles/                 # 已在 M0 完成
  packages/                   # 已在 M0 完成
```

### 关键里程碑
1. **Day 1-2** · `ProjectManager` + `project.json` schema
2. **Day 3-4** · 复用 huobao-drama 的 adapter 接口，实现至少 2 个 image / 2 个 video / 2 个 text / 1 个 tts adapter
3. **Day 5-6** · `GenerationQueue` 单机版（基于 SQLite + asyncio）
4. **Day 7-8** · `skill_loader` + `tool_registry`，把 Skill 中的 tool 调用桥接到 Python 函数
5. **Day 9-10** · CLI `studio new` / `studio run` / `studio compose`
6. **Day 11-12** · 端到端跑通"小说 → 短剧"，输出第 1 个 Demo

### 验收 DoD
- [x] `studio new my-first-drama --novel sample.txt` 可创建项目（`PYTHONPATH=src python -m cli.main new ...`）
- [x] `studio run my-drama --auto` 跑通 14 步骤（含 08a），输出 `output/episode_1_final.mp4`（mock 占位）
- [x] 中间产物正确落盘（analysis/ / scripts/ / assets/ / storyboards/ / output/）
- [x] 状态机支持任意阶段进入与断点续跑（每步落盘 project.json）
- [x] 进入视频生成前有成本关卡（`run` 默认停下给预估，`--yes`/`--auto` 放行）
- [x] 至少支持 2 个图像供应商可切换（`seedream` + `openai`，环境变量驱动）
- [x] 真实 HTTP Adapter 接入（OpenAI 兼容 / Seedream / Seedance / MiniMax）+ 离线单测（`pytest`）
- [ ] 接通真实 Key 后，小型示例总耗时 < 30 分钟 — **待线上联调**
- [ ] `GenerationQueue` lease-based 队列（当前为单进程顺序执行）— **待**

---

## M2 · Web MVP（Week 4-6）

> **目标**：把 CLI MVP 包一层 Web，加上可视化、SSE 实时进度、多项目管理。

### 增量交付物

```
agents-studio-ai/
  src/
    server/                   # ✨ 新增
      app.py                  # FastAPI app
      routers/
        projects.py
        assets.py
        generate.py           # 触发生成（入队）
        tasks.py              # 任务状态 SSE
        events.py             # 项目事件 SSE
        assistant.py          # Agent 对话（SSE 流式）
        config.py             # 模型配置
      services/
        generation_tasks.py
        project_events.py
  frontend/                   # ✨ 新增
    package.json
    src/
      App.tsx
      pages/
        Projects.tsx
        ProjectDetail.tsx     # 项目仪表盘 + Agent 对话
        AssetLibrary.tsx
        StoryboardBoard.tsx   # 分镜看板（仿 Jellyfish）
        Settings.tsx          # 模型配置
      stores/                 # zustand
      services/
        generated/            # OpenAPI 自动生成
  deploy/
    docker-compose.yml        # 单容器
    production/
      docker-compose.yml      # + Postgres
```

### 关键里程碑
1. **Week 4** · FastAPI 框架 + 路由 + SSE
2. **Week 5** · React UI（项目列表 / 项目详情 / 资产库 / 分镜看板）
3. **Week 6** · Docker 部署 + 多项目隔离

### 验收 DoD
- [ ] 浏览器可创建项目、上传小说、对话生成、查看分镜、预览视频
- [ ] SSE 实时显示任务进度（仿 ArcReel）
- [ ] 设置页可配置多个供应商 API Key
- [ ] 一键 docker-compose 部署
- [ ] 默认账号 / 密码登录 + JWT

---

## M3 · Studio 完整版（Week 7-9）

> **目标**：把 ArcReel 级别的工程能力补齐 —— SessionActor、Sandbox、Profile Manifest、版本管理、费用追踪、剪映导出。

### 增量交付物

```
src/
  agent_runtime/
    session_actor.py          # ✨ 仿 ArcReel
    session_store.py          # transcript DB 镜像
    stream_projector.py       # 流式事件 → 前端
    sdk_tools/                # MCP Tool（in-process）
  sandbox/                    # ✨ bwrap + Windows 降级
    sandbox.py
    windows_whitelist.py
  custom_provider/            # ✨ 自定义供应商
    discovery.py              # /v1/models 自动发现
    backend_factory.py
  cost/
    usage_tracker.py          # ✨ 费用追踪
    cost_calculator.py        # 按供应商分策略
  export/
    jianying.py               # ✨ 剪映草稿导出
    pr_xml.py                 # PR / DaVinci XML
  i18n/                       # ✨ zh / en + 翻译框架
```

### 关键里程碑
1. **Week 7** · SessionActor + Sandbox
2. **Week 8** · Custom Provider + 费用追踪 + 版本管理
3. **Week 9** · 剪映导出 + i18n + e2e 测试

### 验收 DoD
- [ ] 多用户并发对话不串台
- [ ] Linux 下 bwrap 沙箱启用，Windows 自动降级到白名单
- [ ] 自定义 OpenAI 兼容供应商可一键添加并自动发现模型
- [ ] 项目设置页显示每个项目实际花费（按供应商）
- [ ] 一键导出剪映草稿 ZIP，剪映打开能正确加载

---

## M4 · 生态集成（Week 10+）

> **目标**：把 Agents Studio 打开成一个生态平台，对接外部 Agent / 工作流 / 编辑工具。

### 候选模块（按优先级）

| 模块 | 价值 | 借鉴 |
|---|---|---|
| **MCP Tool 服务**（暴露给外部 Agent） | ⭐⭐⭐⭐⭐ | ArcReel + OpenClaw |
| **ComfyUI 后端集成** | ⭐⭐⭐⭐ | Pixelle |
| **三层 Agent（决策/执行/监督）** | ⭐⭐⭐⭐ | Toonflow |
| **持久化 Agent 记忆**（向量检索） | ⭐⭐⭐⭐ | Toonflow |
| **章节事件图谱** | ⭐⭐⭐ | Toonflow |
| **可编程供应商**（运行时 TS/Py 适配） | ⭐⭐⭐ | Toonflow |
| **数字人口播 Pipeline** | ⭐⭐⭐ | Pixelle |
| **动作迁移 Pipeline** | ⭐⭐⭐ | Pixelle |
| **CutOS 内置时间线编辑器** | ⭐⭐ | BigBanana |
| **跨平台桌面客户端**（Electron） | ⭐⭐ | Toonflow / moyin |
| **协作 / 多人编辑** | ⭐⭐ | — |

---

## 关键时间节奏（建议）

| 时间 | 状态 | 团队规模 |
|---|---|---|
| Week 1 | M0 完成（仅文档 + Skill） | 1 人 |
| Week 3 | M1 完成（CLI 跑通端到端） | 1-2 人 |
| Week 6 | M2 完成（Web 可对外演示） | 2-3 人 |
| Week 9 | M3 完成（生产可用） | 3-4 人 |
| Week 16 | M4 阶段性 | 4-6 人 |

> **小团队（1-2 人）建议聚焦 M0→M1→M2 三步走**，避免一上来就做 M3 的所有工程能力，先证明产品-市场匹配。

---

## 风险与对冲

| 风险 | 概率 | 影响 | 对冲 |
|---|---|---|---|
| 视频模型 API 频繁变化 | 高 | 中 | Adapter 接口 + 注册表，新增/替换成本极低 |
| 角色一致性不稳定 | 中 | 高 | 6 层身份锚点 + 三视图衍生 + 每次重生留档 |
| 长任务失败 | 中 | 中 | Lease-based 队列 + DLQ + 断点续传 |
| Token 成本失控 | 中 | 中 | 费用追踪 + 预估 + 分供应商策略（仿 ArcReel） |
| 上下文爆炸 | 中 | 高 | Subagent 隔离上下文 + Orchestrator 只收摘要 |
| 用户提示词写不好 | 高 | 中 | 画风包内置高质量模板 + Pre-flight 方案审阅 |

---

## 衡量成功的指标

### 产品指标
- **首次成片时间**（TTFV）：用户从 0 到第 1 个完整短剧 ≤ 30 分钟
- **单集成片成本**：1 集 2 分钟 ≤ ¥150（参照 Toonflow 公开数据）
- **角色一致性主观评分**：6 层锚点全开后，30 分镜内人物身份感知一致 ≥ 90%

### 工程指标
- 单 Skill 平均 < 200 行 Markdown
- Adapter 新增（一个新供应商）≤ 100 行代码
- 端到端 e2e 测试覆盖 11 步骤
- 生产环境 P95 任务排队时延 < 30s

---

## 决策记录

> 这一节会随项目演进持续追加，所有重大架构决策都记录在 `docs/adr/` 下（仿 ArcReel）。

- ADR-001 · 选择 Python + FastAPI + Claude Agent SDK 主路径
- ADR-002 · Skill 使用 huobao-drama 风格的 Markdown，不用 JSON
- ADR-003 · 采纳 ArcReel 的「编排 Skill + 聚焦 Subagent」模式
- ADR-004 · 资产 Spec 中央化（仿 ArcReel `asset_types.py`）
- ADR-005 · Generation Mode 对 LLM 隐藏，由编排层注入
- ADR-006 · 任务队列用自研 Lease-based（不引入 Redis）
- ADR-007 · SessionActor 从 Day 1 引入（不要后期重构）
