from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class UserSession(BaseModel):
    username: str
    is_authenticated: bool = True
    logged_in_at: datetime = Field(default_factory=datetime.utcnow)


class LoginRequest(BaseModel):
    username: str
    password: str
