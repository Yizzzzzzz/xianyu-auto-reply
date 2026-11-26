"""
AI Token 用量记录模型

记录每次 AI 服务商调用的 token 消耗，用于“每个用户过去几天用了多少 token”看板。
用量来自 OpenAI 兼容接口 chat/completions 返回的 usage 字段
（prompt_tokens / completion_tokens / total_tokens），由
AiProviderFailoverService 在每次调用成功后写入。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base_class import Base


class AITokenUsage(Base):
    """AI Token 用量记录表"""

    __tablename__ = "xy_ai_token_usage"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="记录ID")
    owner_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True, comment="平台用户ID")
    provider_name: Mapped[str] = mapped_column(String(80), nullable=False, default="", comment="服务商名称")
    model: Mapped[str] = mapped_column(String(100), nullable=False, default="", comment="模型名称")
    feature: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="",
        comment="功能：generate_listing/pick_competitor/identify_item/chat",
    )
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="输入token数")
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="输出token数")
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="总token数")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        comment="创建时间",
    )

    __table_args__ = (
        Index("ix_ai_token_usage_owner_created", "owner_id", "created_at"),
    )
