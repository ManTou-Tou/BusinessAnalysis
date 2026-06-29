"""应用配置（Pydantic Settings）。

从环境变量 / .env 读取配置。Phase 1 仅包含启动所需的最小集合：
应用环境、MySQL、Redis、Anthropic key。
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    app_env: str = "development"

    # MySQL（本地运行）
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "copilot"
    mysql_password: str = "copilot_pass"
    mysql_db: str = "copilot"

    # 连接池（Phase 8：走配置便于压测调优；默认与既往硬编码一致，行为不变）
    db_pool_size: int = Field(default=10, ge=1)
    db_max_overflow: int = Field(default=20, ge=0)
    db_pool_recycle: int = Field(default=1800, ge=1)  # 秒

    # Redis（本地运行，Celery broker/backend）
    redis_url: str = "redis://localhost:6379/0"

    # LLM (Claude)
    anthropic_api_key: str = ""
    # 评论分类默认模型：生产用 pinned id 便于复现/成本审计（也接受 alias）
    llm_model_classify: str = "claude-haiku-4-5-20251001"
    # 批量分类（Phase 5）
    llm_classify_chunk_size: int = Field(default=25, ge=1)  # 每块（每次 LLM 调用）评论数
    llm_classify_max_per_request: int = Field(default=500, ge=1)  # 单次最多入队
    llm_classify_batch_max_tokens: int = Field(default=4096, ge=256)  # 批量调用输出上限
    llm_classify_rate_limit: str = "60/m"  # llm 队列任务限流（每 worker 进程）
    review_text_max_chars: int = Field(default=2000, ge=1)  # 单条评论截断长度

    # 批量导入（Phase 8）：单次评论导入上限 + Core 批量插入分块大小
    review_import_max_per_request: int = Field(default=5000, ge=1)  # 超出返回 400，提示分批
    bulk_insert_chunk_size: int = Field(default=1000, ge=1)  # 单事务内每次 executemany 行数

    # API 限流（Phase 8：Redis 固定窗口，fail-open）
    ratelimit_enabled: bool = True
    ratelimit_default_limit: int = Field(default=60, ge=1)  # 默认每窗口请求数（写/CRUD）
    ratelimit_default_window_s: int = Field(default=60, ge=1)  # 窗口秒数
    ratelimit_llm_trigger_limit: int = Field(default=20, ge=1)  # LLM 触发/导入类更紧额度

    # 分布式锁（Phase 8）：Beat 派发锁 TTL（取派发最坏耗时安全上界）
    dispatch_lock_ttl_s: int = Field(default=600, ge=1)

    # Excel 导入（Phase 9）
    import_upload_dir: str = "var/imports"  # 上传落盘目录（相对 backend 工作目录，不在 web 根）
    import_max_file_bytes: int = Field(default=20 * 1024 * 1024, ge=1)  # 单文件上限（20MB）
    import_max_row_errors: int = Field(default=200, ge=1)  # 行级错误明细上限（超出只计数）

    # 利润计算（Phase 6，确定性）
    profit_max_orders_per_run: int = Field(default=1000, ge=1)  # 单次 compute 任务最多处理订单数
    profit_calc_version: str = "v1"  # 利润计算口径版本（进幂等键 + agent_tasks 审计）
    # 注：正常费用分摊固定按收入、Σrevenue==0 退化按 quantity（固定在代码内），
    # 不提供全局口径开关，避免绕开分摊分母产生分叉（见 PHASE_6_PLAN.md §5a#2）。

    # 日报（Phase 7：确定性聚合 + LLM 仅总结）
    llm_model_report: str = "claude-sonnet-4-6"  # 报告总结用 Sonnet（DESIGN 行 19）
    report_calc_version: str = "v1"  # 日报口径/prompt 版本（进幂等键 + 审计）
    report_top_n: int = Field(default=5, ge=1)  # top_products / low_profit_products 取前 N
    report_low_profit_margin: float = 0.10  # 低利润阈值（净利率 < 此值视为低利润）
    report_negative_sample: int = Field(default=20, ge=1)  # 喂 LLM 的负面评论条数上限

    @property
    def sqlalchemy_url(self) -> str:
        """SQLAlchemy 连接串（PyMySQL 驱动）。

        用 URL.create 安全转义用户名/密码中的特殊字符（@ : / % 等）。
        """
        return URL.create(
            "mysql+pymysql",
            username=self.mysql_user,
            password=self.mysql_password,
            host=self.mysql_host,
            port=self.mysql_port,
            database=self.mysql_db,
            query={"charset": "utf8mb4"},
        ).render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
