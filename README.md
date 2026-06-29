# AI E-Commerce Copilot

面向 Shopee / TikTok Shop / Lazada 卖家的、Agent 驱动的电商运营平台。

## 仓库结构

```
.
├── backend/     # Python / FastAPI 后端（API + Agent + Celery 任务）
├── frontend/    # React 前端（卖家审核/操作界面，后续阶段开发）
├── project.md   # 需求文档
├── DESIGN.md    # 系统设计与分阶段进度
├── CODEX_REVIEW_POLICY.md  # 评审规则
└── PHASE_*_PLAN.md         # 各阶段方案（先过审再开发）
```

## 运行环境（本地开发）

- **MySQL 8** 与 **Redis**：直接安装在本机运行。
- **后端**：Python 3.12，`backend/` 下用 uvicorn / celery 本地运行。
- **前端**：React（前端阶段开始时在 `frontend/` 脚手架）。
- **LLM**：Claude。
- 容器化部署（Docker）留待后续在虚拟机（Xterminal）上做。

## 开发规则（重要）

1. 任何方案与代码都须先经 **Codex review** 通过后才能采用（见 [CODEX_REVIEW_POLICY.md](./CODEX_REVIEW_POLICY.md)）。
2. 从 Phase 3 起，每个阶段先写 `PHASE_<n>_PLAN.md`（含「为什么这样跑、为什么这样 build」的解释），
   经用户与 Codex 双重过审后，才开始写该阶段代码。

## 进度

- ✅ Phase 1：后端项目骨架（APPROVED）
- ✅ Phase 2：多租户 + CRUD + order_items + keyset 分页（APPROVED）
- ✅ Phase 3：Celery 异步任务系统 + agent_tasks（APPROVED，方案 [PHASE_3_PLAN.md](./PHASE_3_PLAN.md)）
- ✅ Phase 4：Agent Framework + 单条评论分类 Agent（APPROVED，方案 [PHASE_4_PLAN.md](./PHASE_4_PLAN.md)）
- ✅ Phase 5：评论分类批量化（batch + fan-out + llm 限流，APPROVED，方案 [PHASE_5_PLAN.md](./PHASE_5_PLAN.md)）
- ✅ Phase 6：Profit 计算（确定性，非 LLM）+ 商品利润 analytics（方案 + 代码均 APPROVED，方案 [PHASE_6_PLAN.md](./PHASE_6_PLAN.md)）
- ✅ Phase 7：Daily Report（SQL 聚合 + 确定性指标 + LLM 仅总结 + Beat 定时）（方案 + 代码均 APPROVED，方案 [PHASE_7_PLAN.md](./PHASE_7_PLAN.md)）

> MVP 后端 Agent 流水线已完成。后续可选：前端（React）、其余 Agent、部署（Docker/VM）。

详见 [DESIGN.md](./DESIGN.md)、[backend/README.md](./backend/README.md)。
