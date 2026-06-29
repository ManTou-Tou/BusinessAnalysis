from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AccountCreate(BaseModel):
    name: str


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    status: str
    created_at: datetime
