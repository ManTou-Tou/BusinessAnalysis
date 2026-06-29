"""数据库基础设施：engine、session、Base、依赖与连通性检查。

Phase 1 不定义业务模型，仅提供基础设施。Base 供 Alembic 收集元数据，
后续 Phase 2 的 ORM 模型继承它。
"""
from __future__ import annotations

from collections.abc import Iterator

from pymysql.constants import CLIENT
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

# 连接池：MVP 阶段保守配置，后续按压测调优。
# client_flag=FOUND_ROWS：让 UPDATE 的 rowcount 反映「匹配行数」而非「实际改变行数」。
# 代码里大量 `rowcount==1 / >0` 的条件门禁（claim/mark/回写）本意是「是否命中受守卫的行」，
# 而非「值是否变化」。默认（changed-rows）下，重算写入相同值会得到 rowcount=0 → 误判失败/跳过；
# 启用 FOUND_ROWS 后这些守卫语义正确，且对既有 Phase 3/4/5 无副作用（其状态/字段总会变化）。
engine = create_engine(
    settings.sqlalchemy_url,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_recycle=settings.db_pool_recycle,
    future=True,
    connect_args={"client_flag": CLIENT.FOUND_ROWS},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类（Phase 2 起使用）。"""


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：提供请求级 DB session。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def check_db() -> bool:
    """连通性检查，用于 /ready。"""
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return True
