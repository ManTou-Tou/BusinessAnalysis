# 项目说明（CLAUDE.md）

本文件会在每次会话自动加载，Claude Code 必须遵守其中的规则。

## 技术栈 / 开发环境

| 类别 | 使用技术 |
| --- | --- |
| 前端 | React、JavaScript |
| 后端 | Python |
| 数据库 | 本地 MySQL |
| 缓存 | Redis（本地） |
| 容器 | Docker |
| CI/CD | Jenkins |
| 生产环境 | Xterminal（虚拟机） |
| 部署 | 现阶段先用免费服务做测试部署 |

> 写代码、给方案、选依赖、写部署脚本时，都要以上述技术栈为准；如需引入栈以外的技术，必须先说明原因并经过下方的 Codex review。

## 仓库结构与运行方式

- 代码分为 `backend/`（Python/FastAPI）与 `frontend/`（React）两个目录。
- 本地开发：**MySQL 与 Redis 装在本机运行**（不用 Docker 容器跑）；后端用 uvicorn/celery 本地运行。
- Docker 容器化留待后续在虚拟机（Xterminal）上做部署。

## 强制规则：Codex Review

**无论 Claude Code 做任何方案（plan）或编写/构建代码（build code），都必须先经过我的 Codex review，确认没有问题之后，才可以被采用、使用或实际执行。**

流程：
1. 先出方案（思路、改动文件、预期影响），再写代码。
2. 方案和代码都交给我的 Codex review。
3. review 未通过前：不得当作最终方案，不得合并 / 部署 / 投入使用。
4. Codex 明确「没有问题」后，才放行使用。
5. review 之后若有修改，需重新经过 Codex review。

## 分阶段开发流程（Phase 3 起）

每个开发阶段在写代码前，先产出 `PHASE_<n>_PLAN.md`，内容须包含：
1. 该阶段要做什么、改动哪些文件；
2. **为什么用这种逻辑跑**（运行/数据流/任务编排的理由）；
3. **为什么要这样 build 代码**（结构/取舍/权衡的理由）。

该 MD 须先经**用户过审**，再经 **Codex 过审**，两者都通过后才开始写该阶段代码。

详见 [CODEX_REVIEW_POLICY.md](./CODEX_REVIEW_POLICY.md)。
