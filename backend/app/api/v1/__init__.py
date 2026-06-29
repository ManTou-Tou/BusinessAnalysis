"""v1 路由聚合。"""
from fastapi import APIRouter

from app.api.v1 import (
    accounts,
    agent_tasks,
    agents,
    analytics,
    imports,
    orders,
    products,
    reviews,
    shops,
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(accounts.router)
api_router.include_router(shops.router)
api_router.include_router(products.router)
api_router.include_router(orders.router)
api_router.include_router(reviews.router)
api_router.include_router(agent_tasks.router)
api_router.include_router(agents.router)
api_router.include_router(analytics.router)
api_router.include_router(imports.router)
