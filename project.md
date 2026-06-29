Project: AI E-Commerce Copilot

我要开发一个高并发、大数据、Agent 驱动的电商运营平台。

目标用户：
Shopee / TikTok Shop / Lazada 卖家。

核心目标：
帮助卖家自动分析商品、评论、订单、利润，并生成运营建议。

不是普通 CRUD 系统，而是一个真实生产级后端项目，需要包含：
FastAPI + MySQL + Redis + Celery + Agent Workflow。

---

## Business Goal

传统卖家流程：

卖家
↓
上架商品
↓
写商品标题和描述
↓
看评论
↓
看销量
↓
算利润
↓
决定补货 / 降价 / 打广告

未来 Agent 流程：

Agent
↓
分析商品
↓
生成标题和描述
↓
分析评论
↓
分析销量
↓
计算利润
↓
生成运营建议

卖家只负责审核和执行。

---

## MVP Scope

第一阶段只做后台系统，不需要真实接 Shopee API，可以先手动导入数据。

必须支持：

1. 店铺管理
2. 商品管理
3. 订单管理
4. 评论管理
5. 成本和利润管理
6. Agent 任务系统
7. 每日运营报告

---

## Core Tables

### shops

字段：

id
platform
shop_name
shop_id
status
created_at
updated_at

platform 示例：

shopee
tiktok_shop
lazada

---

### products

字段：

id
shop_id
product_name
sku
category
price
cost
stock
status
created_at
updated_at

status:

active
inactive
out_of_stock

---

### orders

字段：

id
shop_id
product_id
order_no
quantity
sale_price
shipping_fee
platform_fee
ad_cost
total_amount
profit
order_status
created_at

order_status:

pending
paid
shipped
completed
cancelled
refunded

---

### reviews

字段：

id
shop_id
product_id
order_id
buyer_name
rating
review_text
review_type
sentiment
created_at

review_type:

positive
negative
logistics_issue
quality_issue
price_issue
feature_question
spam

sentiment:

positive
neutral
negative

---

### daily_metrics

字段：

id
shop_id
product_id
date
views
clicks
orders
revenue
profit
ad_spend
conversion_rate

---

### agent_tasks

字段：

id
task_type
status
input_json
output_json
error_message
started_at
finished_at
created_at

task_type:

product_analysis
title_generation
review_classification
reply_generation
profit_analysis
daily_report

status:

pending
running
completed
failed

---

## Agent System

设计一个 Agent Framework。

所有 Agent 都必须继承 BaseAgent。

BaseAgent 需要支持：

run()
validate_input()
handle_error()
save_result()

---

## Agent 1: Product Analysis Agent

职责：
分析商品卖点、目标客户、竞争优势、风险点。

输入：

product_name
category
price
cost
description

输出：

selling_points
target_audience
competitive_advantage
risk_points
improvement_suggestions

---

## Agent 2: Listing Writer Agent

职责：
生成电商商品标题、描述、卖点、关键词。

输入：

product_name
category
selling_points
platform

输出：

title
description
bullet_points
keywords
hashtags

需要根据不同平台生成不同风格：

Shopee:
偏搜索关键词和转化

TikTok Shop:
偏短视频卖点和冲动购买

Lazada:
偏正式、清楚、可信

---

## Agent 3: Review Classifier Agent

职责：
批量分析评论。

输入：

reviews[]

输出：

每条评论的：

review_type
sentiment
summary
need_reply

分类：

positive
negative
logistics_issue
quality_issue
price_issue
feature_question
spam

目标性能：

支持批量处理 10000 reviews/minute。

---

## Agent 4: Reply Generator Agent

职责：
根据评论生成客服回复建议。

输入：

review_text
review_type
sentiment
shop_profile

输出：

reply_text
confidence_score

规则：

投诉类评论不能直接自动发送，只能生成建议。
普通问题可以自动回复，但需要保留人工审核开关。

---

## Agent 5: Profit Analysis Agent

职责：
分析商品真实利润。

输入：

sale_price
cost
shipping_fee
platform_fee
ad_cost
quantity

输出：

gross_profit
net_profit
profit_margin
break_even_price
recommendation

示例结论：

这个商品看起来销量很好，但扣除广告和平台费用后，净利润太低，建议提高售价或降低广告预算。

---

## Agent 6: Daily Report Agent

职责：
生成店铺每日运营报告。

输入：

orders
reviews
daily_metrics
profit_data

输出：

daily_summary
top_products
low_profit_products
negative_review_summary
recommended_actions

---

## System Architecture

使用生产级架构：

Frontend
↓
FastAPI
↓
MySQL

FastAPI
↓
Redis

FastAPI
↓
Celery

Celery Workers
↓
Agent Service

Agent Service
↓
LLM API

---

## Redis Usage

Redis 用于：

1. Celery Broker
2. Cache
3. Rate Limiting
4. Distributed Lock
5. Agent Task Status

---

## Celery Tasks

需要设计以下任务：

sync_product_data
sync_order_data
sync_review_data
classify_reviews_batch
generate_product_listing
analyze_profit
generate_daily_report

每个任务需要：

task_id
retry
timeout
logging
error handling

---

## API Requirements

设计 REST API：

> 注：以下路径为需求草图，实际实现统一挂载前缀 `/api/v1`（如 `GET /api/v1/agent/tasks/{id}`）。

### Shop APIs

POST /shops
GET /shops
GET /shops/{shop_id}

### Product APIs

POST /products
GET /products
GET /products/{product_id}
PUT /products/{product_id}

### Order APIs

POST /orders
GET /orders
GET /orders/{order_id}

### Review APIs

POST /reviews/import
GET /reviews
POST /reviews/classify

### Agent APIs

POST /agent/product-analysis
POST /agent/listing-writer
POST /agent/review-classifier
POST /agent/reply-generator
POST /agent/profit-analysis
POST /agent/daily-report
GET /agent/tasks/{id}

### Analytics APIs

GET /analytics/shop/{shop_id}/daily
GET /analytics/product/{product_id}/profit
GET /analytics/shop/{shop_id}/report

---

## High Concurrency Requirements

目标支持：

1000 shops
100000 products
1000000 orders
10000000 reviews
1000000 agent_tasks/day

必须考虑：

分页查询
索引优化
批量插入
批量更新
异步任务
任务重试
任务去重
分布式锁
API 限流

---

## Database Requirements

使用 MySQL 8。

需要设计：

表结构
索引
外键关系
分页查询
批量写入
事务
连接池

未来扩展：

Read Replica
Partition Table
Sharding
Elasticsearch for search

---

## Development Requirements

技术栈：

Python 3.12
FastAPI
MySQL 8
SQLAlchemy
Alembic
Redis
Celery
Pydantic
Docker
Docker Compose
pytest

代码要求：

Clean Architecture
Repository Pattern
Service Layer
Dependency Injection
Type Hints
Unit Tests
Integration Tests
Structured Logging

---

## Project Structure

请帮我生成完整项目结构，例如：

app/
  api/
  core/
  models/
  schemas/
  repositories/
  services/
  agents/
  tasks/
  workers/
  utils/
tests/
docker-compose.yml
Dockerfile
alembic/
README.md

---

## Roadmap

请按照以下阶段帮我开发：

Phase 1:
项目结构 + Docker + MySQL + Redis + FastAPI 启动

Phase 2:
Shop / Product / Order / Review CRUD

Phase 3:
Celery 异步任务系统

Phase 4:
Agent Framework

Phase 5:
Review Classifier Agent

Phase 6:
Profit Analysis Agent

Phase 7:
Daily Report Agent

Phase 8:
高并发优化、索引优化、批量处理、限流

---

请先不要一次性写完所有代码。

请先输出：
1. 系统架构设计
2. 数据库 ERD
3. 项目目录结构
4. 开发步骤
5. 第一阶段需要创建的文件

然后等codex确认后，再开始生成代码。