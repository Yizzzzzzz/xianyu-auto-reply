"""
AI 服务商配置模型

功能：
1. 定义 AI 服务商配置表结构（xy_ai_provider_configs）
2. 记录用于容灾调用的 OpenAI 兼容接口配置
3. 存储 Fernet 加密后的 api_key 密文
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from common.db.base_class import Base, TimestampMixin


class AiProviderConfig(TimestampMixin, Base):
    """AI 服务商配置表 - 记录容灾调用的第三方模型接口与状态"""

    __tablename__ = "xy_ai_provider_configs"
    __table_args__ = (
        Index("idx_aipc_enabled_priority", "is_enabled", "priority"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True, comment="主键ID")
    name: Mapped[str] = mapped_column(String(80), nullable=False, comment="服务商名称")
    base_url: Mapped[str] = mapped_column(String(300), nullable=False, comment="接口基础地址")
    api_key: Mapped[str] = mapped_column(Text, nullable=False, comment="接口密钥（Fernet密文）")
    model: Mapped[str] = mapped_column(String(100), nullable=False, comment="默认模型名称")
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, comment="是否启用"
    )
    priority: Mapped[int] = mapped_column(
        Integer, nullable=False, default=100, comment="调用优先级（越小越先试）"
    )
    timeout_seconds: Mapped[int] = mapped_column(
        Integer, nullable=False, default=30, comment="单次调用超时时间（秒）"
    )
    last_error: Mapped[str | None] = mapped_column(
        String(1000), default=None, comment="最近一次失败错误摘要"
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime, default=None, comment="最近一次成功调用时间"
    )
