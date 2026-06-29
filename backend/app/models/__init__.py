"""ORM 模型聚合（供 Alembic autogenerate 收集元数据）。"""
from app.models.account import Account
from app.models.agent_task import AgentTask
from app.models.daily_metrics import DailyReport, ProductDailyMetric, ShopDailyMetric
from app.models.order import Order, OrderItem
from app.models.product import Product
from app.models.review import Review
from app.models.shop import Shop

__all__ = [
    "Account",
    "Shop",
    "Product",
    "Order",
    "OrderItem",
    "Review",
    "AgentTask",
    "ShopDailyMetric",
    "ProductDailyMetric",
    "DailyReport",
]
