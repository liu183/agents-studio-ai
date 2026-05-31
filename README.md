# Agents Studio AI

> 一个面向 **AI 短剧 / 漫剧全流程创作** 的 Agent Studio 平台 —— 从小说一键到成片。

本仓库从 **设计与原型阶段** 起步，现已落地 **M1 · CLI MVP 的离线 mock 骨架**。它由四块组成：

1. **洞察分析** — `docs/INSIGHTS.md`：对 9 个一线开源项目（Toonflow / huobao-drama / ArcReel / Jellyfish / moyin-creator / waoowaoo / BigBanana / lumenx / Pixelle-Video）做了深度拆解、对比和复用建议。
2. **平台架构** — `docs/ARCHITECTURE.md` + `docs/ROADMAP.md`：给出推荐的核心模块、技术栈、目录结构、分阶段开发计划。
3. **Agent Skills 原型** — `skills/`：可立即放进任何 Claude Agent SDK / Mastra / 自研 Agent Runtime 的 SKILL.md 集合。一套 14 个 Skill 串起从「小说 → 剧本 → 资产 → 分镜 → 关键帧 → 视频片段 → 配音 → 成片」的完整流水线，无需后端就能先在 Agent 端把流程跑通。
4. **CLI 运行时（M1）** — `src/`：一个**纯标准库、可离线运行**的 Python 实现，把 Orchestrator 状态机写成代码，能创建项目、加载 Skill、按状态机 dispatch，并以 **mock 模式** 端到端产出 `analysis/ → scripts/ → assets/ → storyboards/ → output/` 全套落盘产物。真实模型 Adapter 是 M1 的后续增量（见 `docs/ROADMAP.md`）。

## 快速开始（M1 CLI · mock 模式）

> 无需任何第三方依赖、无需联网、无需 API Key。默认 Python ≥ 3.9。

```bash
# 1) 用示例小说创建一个项目
PYTHONPATH=src python -m cli.main new my-drama --novel examples/sample_novel.txt

# 2) 查看状态机检测到的下一步
PYTHONPATH=src python -m cli.main status my-drama

# 3) 驱动流水线（默认会在「视频生成」这个高成本关卡停下来给预估）
PYTHONPATH=src python -m cli.main run my-drama

# 4) 确认成本后跑完第 1 集，产出 projects/my-drama/output/episode_1_final.mp4
PYTHONPATH=src python -m cli.main run my-drama --yes

# 也可以：一步步走 / 自动跑完 / 单独制作付费集
PYTHONPATH=src python -m cli.main next my-drama
PYTHONPATH=src python -m cli.main run  my-drama --auto
PYTHONPATH=src python -m cli.main compose my-drama --episode 8   # 付费钩子集（首尾帧）

# 辅助命令
PYTHONPATH=src python -m cli.main skills      # 列出 skills/ 下加载到的 14 个 SKILL.md
PYTHONPATH=src python -m cli.main providers   # 列出已注册的 Provider Adapter（当前仅 mock）
```

安装为 `studio` 命令（可选，需要 setuptools，可离线）：`pip install -e .` 后即可直接 `studio run my-drama --auto`。

> **mock 模式说明**：默认无任何凭证时，所有图像/视频/配音都由 `src/backends/mock.py` 的占位 Adapter 产出（写入带 `.png/.mp4/.wav` 后缀的占位文件），用于验证**编排正确性与产物落盘结构**。

## 接入真实供应商

运行时会按 **环境变量** 解析每种媒体使用哪个供应商：**显式选择 > 环境变量 > 系统默认**；若所选真实供应商缺少凭证，则自动回退到 mock，保证流水线永远能跑。配置好凭证后，**同一套编排步骤无需改动**即可产出真实素材（图片/视频会被下载、配音音频会从 hex 解码后落盘）。

已内置的真实 Adapter（`build_request` / `parse_response` 分离，可离线单测）：

| 媒体 | 供应商 | Adapter | 说明 |
|---|---|---|---|
| 文本 | OpenAI 兼容 | `openai_compat` | GPT / Qwen / DeepSeek / vLLM 等，`/chat/completions` |
| 图像 | Seedream（火山方舟）| `seedream` | `/images/generations`，支持图生图/多图融合 |
| 图像 | OpenAI 兼容 | `openai` | `gpt-image-1` / DALL·E，`/images/generations` |
| 视频 | Seedance（火山方舟）| `seedance` | 异步任务 + 轮询，支持首尾帧 |
| 视频 | MiniMax Hailuo | `minimax_video` | 异步：提交 → 查询 → 取回下载链接 |
| 配音 | MiniMax T2A v2 | `minimax` | 同步，返回 hex 音频 |

```bash
# 火山方舟（Seedream 图像 + Seedance 视频）
export ARK_API_KEY=...           # 必填
export ARK_IMAGE_MODEL=doubao-seedream-4-0-250828
export ARK_VIDEO_MODEL=doubao-seedance-1-0-pro-250528

# OpenAI 兼容（文本，可选换图像）
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=https://api.openai.com/v1
export OPENAI_TEXT_MODEL=gpt-4o-mini

# MiniMax（配音 / 可选视频）
export MINIMAX_API_KEY=...
export MINIMAX_GROUP_ID=...

# 选择具体供应商（可选；默认 image=seedream video=seedance text=openai_compat tts=minimax）
export STUDIO_IMAGE_PROVIDER=seedream

PYTHONPATH=src python -m cli.main providers          # 查看「已注册」与「当前生效」
PYTHONPATH=src python -m cli.main run my-drama --yes # 用真实供应商产出
```

> 沙箱/CI 离线时，真实 Adapter 通过 `FakeTransport` + 预置 JSON 做**完整离线单测**（见 `tests/`）：`PYTHONPATH=src python -m pytest -q`。

## 双通道任务队列（GenerationQueue）

每集要生成几十到上百张图片 / 视频片段 / 配音；这些步骤通过**双通道、lease-based** 任务队列并发执行（仿 ArcReel `GenerationQueue` + 09-video-generator SKILL）：

- **通道隔离**：`image_channel` (default concurrency=4, RPM=20) / `video_channel` (concurrency=2, RPM=4) / `tts_channel` (concurrency=4, RPM=60)
- **状态机**：`pending → leased → running → succeeded | failed → retry/dlq`，超出 `max_attempts` 进入 DLQ
- **崩溃可恢复**：每次状态变化即写入 `project.json` 的 `queue` 字段；启动时 `recover_expired()` 把过期租约的任务回滚到 pending
- **限流**：每通道独立的 RPM token-bucket（设为 `0` 即关闭）
- **可观测**：`studio queue <name>` 输出每通道 pending/leased/running/success/failed/dlq 计数；`studio queue <name> retry-dlq` 把 DLQ 全量重排队

```bash
# 调整并发与限流（默认值在大多数供应商上是安全的）
export STUDIO_IMAGE_CONCURRENCY=8   STUDIO_IMAGE_RPM=60
export STUDIO_VIDEO_CONCURRENCY=4   STUDIO_VIDEO_RPM=10
export STUDIO_TTS_CONCURRENCY=8     STUDIO_TTS_RPM=120

PYTHONPATH=src python -m cli.main run my-drama --auto
PYTHONPATH=src python -m cli.main queue my-drama          # 查看任务统计
PYTHONPATH=src python -m cli.main queue my-drama retry-dlq # 重排队所有 DLQ 任务
```




## 项目定位

| 维度 | 取向 |
|---|---|
| 形态 | **Agent-Native Studio** —— 不是传统的「按钮 + 工作流」工具，而是「编排 Skill + 聚焦 Subagent + 多供应商 Adapter」的可对话生产平台 |
| 用户 | 短剧 / 漫剧 / 小说改编 / MCN 批量生产 / 个人创作者 |
| 输出 | 单集 30s–3min 的短剧成片 + 剪映草稿 + 工程文件可二次创作 |
| 模型 | 多供应商可切换：文本（GPT / Claude / Gemini / Qwen / DeepSeek / 豆包）+ 图像（Nano Banana / Seedream / GPT Image / Flux / 通义万相）+ 视频（Sora / Veo / Kling / Seedance / Vidu / Wan）+ TTS（MiniMax / Index / Edge） |

## 怎么读这个仓库

- **想立刻跑通端到端流程** → 看上面的 [快速开始](#快速开始m1-cli--mock-模式)，用 `src/` 下的 CLI 在本地离线跑一遍
- **想理解为什么这么设计** → 先读 [`docs/INSIGHTS.md`](docs/INSIGHTS.md)，再读 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- **想立刻试用 Skills** → 直接看 [`skills/README.md`](skills/README.md)，把整个 `skills/` 目录拷进任何 Claude Agent SDK / Cursor / Cline / Kiro 项目即可生效
- **想看开发顺序** → 读 [`docs/ROADMAP.md`](docs/ROADMAP.md)
- **想扩展画风** → 参考 [`art-styles/`](art-styles/) 目录的样例

## 一句话总结

> **不要先写后端再加 Agent，而是先把 Skill 写对、把流程跑通，再用最少的代码长出后端。**
> 这正是 ArcReel / Toonflow / huobao-drama 这类领先项目的共同选择。

## 致谢

本设计深度参考了以下开源项目（按字母序）：[ArcReel](https://github.com/ArcReel/ArcReel)、[BigBanana-AI-Director](https://github.com/shuyu-labs/BigBanana-AI-Director)、[huobao-drama](https://github.com/chatfire-AI/huobao-drama)、[Jellyfish](https://github.com/Forget-C/Jellyfish)、[lumenx](https://github.com/alibaba/lumenx)、[moyin-creator](https://github.com/MemeCalculate/moyin-creator)、[Pixelle-Video](https://github.com/AIDC-AI/Pixelle-Video)、[Toonflow-app](https://github.com/HBAI-Ltd/Toonflow-app)、[waoowaoo](https://github.com/saturndec/waoowaoo)。

每个项目的具体借鉴点见 [`docs/INSIGHTS.md`](docs/INSIGHTS.md)。
