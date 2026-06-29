# PHASE 10 方案：前端最小骨架（React + Vite + Ant Design）

> 状态：**草案，待用户过审 → Codex 过审**。两者通过前不写前端代码（遵 CLAUDE.md / AGENTS.md / CODEX_REVIEW_POLICY.md）。
> 目标：搭起 `frontend/` React 工程，打通「账号切换 → 选店铺 → 看商品列表 → Excel 导入并看任务结果」这条最小前后端联调链路。

---

## 0. 范围（本阶段做 / 不做）

**做（最小骨架，经用户确认）：**
1. `frontend/` 用 **Vite + React 18 + JavaScript(JSX)** 脚手架。
2. 全局：账号切换（设 `X-Account-Id`）、店铺选择器、统一 API 客户端、路由、查询缓存。
3. 三个页面：
   - **商品列表**（`GET /products?shop_id=`，keyset 分页）
   - **Excel 导入页**（`POST /imports/{entity}` 上传 + 轮询 `GET /agent/tasks/{id}` 看 `output_json`）
   - （承载上两者所必需的）**店铺列表/选择**（`GET /shops`）
4. **后端捆绑改动**：`app/main.py` 增加 `CORSMiddleware`（dev 放行 Vite 源），`config.py` 增 `cors_origins` 配置项。

**不做（留后续 Phase）：**
- 订单/评论页、评论分类结果、利润 analytics、每日报告、Agent 触发面板（先骨架，验证联调跑通再迭代）。
- 正式登录/鉴权（MVP 仍用 `X-Account-Id` 头占位）。
- 前端构建/部署（Docker、托管）——留部署阶段。
- TypeScript（遵技术栈「React、JavaScript」，本阶段用 JSX）。

---

## 1. 要改动 / 新增的文件

### 后端（捆绑小改动，需 Codex 审）
| 文件 | 改动 | 理由 |
| --- | --- | --- |
| `backend/app/core/config.py` | 增 `cors_origins: list[str]`（env `CORS_ORIGINS`，默认 `["http://localhost:5173"]`） | 跨域源走配置，不硬编码；prod 可收紧 |
| `backend/app/main.py` | 挂 `fastapi.middleware.cors.CORSMiddleware`，allow 上述源 + `X-Account-Id` 头 + 常用方法 | 否则浏览器跨域预检/请求被拦，前端任何调用都失败 |
| `backend/.env.example` | 增 `CORS_ORIGINS=http://localhost:5173` 占位 | 与配置项对齐 |

### 前端（新增）
```
frontend/
  index.html
  package.json
  vite.config.js                # dev server 5173；（可选）/api 代理到 8000
  .env.example                  # VITE_API_BASE_URL=http://localhost:8000
  .gitignore                    # node_modules/ dist/
  src/
    main.jsx                    # 挂 React Query Provider + Router + antd ConfigProvider
    App.jsx                     # 布局：顶栏(账号输入+店铺选择) + 侧栏菜单 + 路由出口
    api/
      client.js                 # axios 实例：baseURL + 拦截器注入 X-Account-Id；429/4xx 统一处理
      shops.js / products.js / imports.js / tasks.js   # 各资源的请求函数
    context/
      AccountContext.jsx        # 当前 account_id（localStorage 持久化）
      ShopContext.jsx           # 当前 shop_id（依赖 account，切账号清空）
    hooks/
      useProducts.js / useShops.js / useImportTask.js   # 封装 React Query
    pages/
      ProductsPage.jsx          # antd Table + 分页（next_cursor）
      ImportPage.jsx            # antd Upload + 实体选择 + 任务结果展示（轮询）
    components/
      AccountSwitcher.jsx / ShopSelector.jsx / TaskResult.jsx
    styles/ (按需)
```

---

## 2. 为什么用这种逻辑跑（运行 / 数据流）

1. **租户标识 = `X-Account-Id` 头（沿用后端 MVP 约定）**：前端用 `AccountContext` 存当前 account（localStorage 持久化），axios **请求拦截器**统一注入该头。后端无正式登录，前端不自造 token，保持与后端契约一致。
2. **店铺是二级上下文**：`products` 列表与 `imports`（非 shops 实体）都**必须带 `shop_id`**。所以进应用先 `GET /shops` 选一个店铺存 `ShopContext`；切账号时清空店铺，避免越权请求他人店铺（后端也会再校验归属，前端只是体验）。
3. **列表用 keyset 分页**：后端返回 `{ items, next_cursor }`，`next_cursor` 为 `null` 即末页。前端「加载更多 / 下一页」用 `?cursor=<next_cursor>&limit=` 续拉，不做深 offset，对齐后端设计。
4. **Excel 导入是异步两段式**：
   - 上传 `POST /imports/{entity}`（multipart：`file` + `shop_id` +（reviews 必带）`conflict=insert`）→ 拿到 `202` 的任务 `id`；
   - 轮询 `GET /agent/tasks/{id}` 直到 `status ∈ {succeeded, failed, cancelled}`，展示 `output_json.{inserted,updated,error_count,errors,processed_rows}`；
   - **强调异步**：上传成功 ≠ 入库完成，需 worker 跑完；前端用轮询表达这一过程（间隔 1.5s，到终态停）。
5. **服务端状态交给 TanStack Query**：列表缓存、导入任务轮询（`refetchInterval` 到终态置 false）、变更后失效（导入成功→失效商品列表）。本地 UI 状态（当前账号/店铺）用 Context，二者分离。

## 3. 为什么要这样 build（结构 / 取舍）

1. **Vite（而非 Next.js / CRA）**：这是纯内部运营后台 SPA，无 SEO/SSR 需求；Vite 启动快、配置轻，且 `frontend/README.md` 已先行约定 Vite。Next.js 的 SSR/路由约定对本场景是过度工程。
2. **Ant Design 5（用户已选）**：卖家后台是**表格/表单/分页/上传**密集型，AntD 的 `Table / Form / Upload / Pagination / Select` 开箱即用，出页最快，少造轮子。代价：包体较大、视觉偏「企业风」——MVP 阶段可接受。
3. **JavaScript(JSX) 而非 TS**：严格遵技术栈表（「React、JavaScript」）。后续若要 TS 需另走栈变更 + Codex 审。
4. **分层目录（api / hooks / context / pages / components）**：把「请求」「服务端状态」「全局 UI 状态」「页面」「展示组件」分开，方便后续加订单/评论/报告页时按同一模式扩展，不返工。
5. **axios 集中拦截**：头注入、`429 Retry-After` 与错误提示集中处理（后端 Phase 8 已上限流，前端要能优雅提示），避免每个请求各写一遍。
6. **后端 CORS 为何必须现在加**：Vite(5173) 与 API(8000) 跨源，浏览器会拦截预检与带自定义头（`X-Account-Id`）的请求。不加则前端一行都跑不通。放配置 + 默认仅放行本地 dev 源，安全可控。
   - 备选：用 Vite `server.proxy` 把 `/api` 代理到 8000 规避 CORS。**本方案选「后端加 CORS」为主**（更贴近未来前后端分离部署的真实形态），proxy 作为可选降级（写进 vite.config.js 注释）。

---

## 4. 栈外依赖说明（AGENTS.md 要求逐一说明理由）

| 依赖 | 用途 | 为何引入 |
| --- | --- | --- |
| `react` / `react-dom` | 框架本体 | 技术栈指定 |
| `vite` / `@vitejs/plugin-react` | 构建/dev server | README 已定；轻快 |
| `react-router-dom` | 客户端路由 | 多页面导航的事实标准，体积小 |
| `@tanstack/react-query` | 服务端状态（缓存/轮询/失效） | 导入轮询、列表缓存的标准解法，避免手写 loading/error/重试 |
| `axios` | HTTP 客户端 | 拦截器统一注入头/错误处理比 fetch 简洁 |
| `antd` | UI 组件库 | 用户已选；表格/表单密集型后台出页最快 |

> 以上均为前端常规栈，非生产基础设施变更。若 Codex 认为某项可去（如用原生 fetch 替 axios、用 SWR 替 react-query），可在 review 中指出，我据此调整后重审。

---

## 5. 验收标准（本阶段 Done 的定义）

1. `frontend/` 下 `npm install && npm run dev` 起得来，访问 `http://localhost:5173`。
2. 顶栏输入/切换 account_id 后，店铺选择器能拉到 `GET /shops` 列表并选定。
3. 商品列表页能按所选店铺展示 `GET /products?shop_id=` 数据，分页（next_cursor）可用。
4. 导入页能上传 `products.xlsx` 等，轮询展示任务从 `pending → succeeded` 及 `inserted/error_count/errors`。
5. 后端 `/products` 等接口在浏览器跨域调用下不被 CORS 拦截。
6. 越权防护沿用后端：切到不属于本 account 的 shop_id 时，后端 4xx，前端有错误提示（不崩）。

---

## 6. 风险 / 开放问题（请 Codex 重点看）

1. **鉴权占位**：`X-Account-Id` 明文头仅 MVP，前端不做真正鉴权；是否接受先骨架后补？
2. **CORS 方案二选一**：后端加 `CORSMiddleware`（主）vs Vite proxy（降级）——确认主方案。
3. **轮询而非 WebSocket**：导入进度用 HTTP 轮询（1.5s）。MVP 够用；高频/大文件再议。
4. **依赖集**：`react-query + axios + antd` 是否都放行，或要求精简。
5. **本阶段不含订单/评论/报告页**——确认范围收敛 OK。

---

## 7. 下一步（双过审后）

1. 用户过审本 MD → 你的 Codex 过审本 MD。
2. 两者通过后：我先做后端 CORS 小改 → 交 Codex 审；通过后 scaffold `frontend/` 代码 → 再交 Codex 审。
3. 全绿后联调，按验收标准走一遍。
