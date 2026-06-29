# AI E-Commerce Copilot — Frontend

React 前端（卖家审核与操作界面）。

## 状态：占位（尚未开发）

按 MVP 范围，第一阶段聚焦后端（见 [../DESIGN.md](../DESIGN.md)）。
前端在后续「前端阶段」开始时再脚手架，届时会先写方案 MD 过审，再开发。

## 规划技术栈

- React + JavaScript
- 通过 `X-Account-Id` 头（后续替换为正式登录/Token 鉴权）调用后端 `/api/v1` 接口
- 构建/部署：本地开发用 Vite dev server；生产前端托管方案在前端阶段确定

## 计划首批页面（草案，待方案细化）

- 店铺 / 商品 / 订单 / 评论列表与详情
- 评论分类结果查看
- 每日运营报告查看
- Agent 任务触发与状态查看
