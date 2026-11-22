"""
AI 服务商配置与容灾管理路由

提供管理员专用的 AI 服务商配置 CRUD、排序、连通性测试接口。
所有接口均需管理员权限（get_current_admin_user）。
API Key 入库前采用 Fernet 对称加密，出库时均脱敏掩码，绝不返回明文 Key。
"""
from __future__ import annotations

from datetime import datetime
import base64
from typing import Any, List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_admin_user, get_db_session
from app.core.config import get_settings
from app.services.ai_provider_crypto import (
    decrypt_api_key,
    encrypt_api_key,
    mask_api_key,
)
from app.services.ai_provider_failover_service import (
    AiProviderFailoverService,
    AllProvidersFailedError,
    NoProviderConfiguredError,
)
from common.models.ai_provider_config import AiProviderConfig
from common.models.user import User
from common.services.ai_provider_service import build_openai_url, extract_response_error

router = APIRouter(prefix="/ai", tags=["AI服务商配置"])
settings = get_settings()


# ==================== 请求与响应模型 ====================


class AiProviderCreateRequest(BaseModel):
    """创建 AI 服务商配置请求"""

    name: str = Field(..., min_length=1, max_length=80, description="服务商名称")
    base_url: str = Field(..., min_length=1, max_length=300, description="接口基础地址")
    api_key: str = Field(..., min_length=1, description="接口密钥（明文，入库前加密）")
    model: str = Field(..., min_length=1, max_length=100, description="默认模型名称")
    is_enabled: bool = Field(True, description="是否启用")
    priority: int = Field(100, ge=0, description="调用优先级（越小越先试）")
    timeout_seconds: int = Field(30, ge=1, le=300, description="单次调用超时时间（秒）")


class AiProviderUpdateRequest(BaseModel):
    """更新 AI 服务商配置请求

    规则：
    - api_key 提交为空字符串、None 或等于当前掩码值时，表示保持原 Key 不变，不重新加密覆盖。
    - 若提交了新的明文 Key，则使用 Fernet 密钥加密后覆盖原密文。
    """

    name: Optional[str] = Field(None, min_length=1, max_length=80, description="服务商名称")
    base_url: Optional[str] = Field(None, min_length=1, max_length=300, description="接口基础地址")
    api_key: Optional[str] = Field(
        None,
        description="接口密钥。留空或传入掩码值表示保持原 Key 不变；传入新明文则加密更新",
    )
    model: Optional[str] = Field(None, min_length=1, max_length=100, description="默认模型名称")
    is_enabled: Optional[bool] = Field(None, description="是否启用")
    priority: Optional[int] = Field(None, ge=0, description="调用优先级（越小越先试）")
    timeout_seconds: Optional[int] = Field(None, ge=1, le=300, description="单次调用超时时间（秒）")


class AiProviderReorderRequest(BaseModel):
    """AI 服务商重新排序请求"""

    ids: List[int] = Field(..., min_length=1, description="有序的服务商 ID 列表")


class AiProviderItemResponse(BaseModel):
    """AI 服务商返回模型（api_key 字段为脱敏掩码）"""

    id: int
    name: str
    base_url: str
    api_key: str = Field(..., description="已脱敏的密钥掩码，绝不返回明文 Key")
    model: str
    is_enabled: bool
    priority: int
    timeout_seconds: int
    last_error: Optional[str] = None
    last_used_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class AiProviderTestResponse(BaseModel):
    """服务商连通性测试响应"""

    ok: bool
    status: int
    message: str


class GenerateListingResponse(BaseModel):
    """生成商品文案响应"""

    title: str = Field(..., description="生成的商品标题")
    description: str = Field(..., description="生成的商品描述详情")
    category: str = Field(..., description="生成的商品分类")
    provider_used: str = Field(..., description="本次成功调用的服务商名称")


# ==================== 辅助函数 ====================


def _create_async_client(timeout: float) -> httpx.AsyncClient:
    """创建异步 HTTP 客户端，在代理环境变量格式不兼容时自动降级"""
    try:
        return httpx.AsyncClient(timeout=timeout)
    except Exception:
        return httpx.AsyncClient(timeout=timeout, trust_env=False)


def _get_encryption_key() -> str:
    """获取对称加密密钥，缺失时抛出明确异常"""
    key = settings.ai_provider_encryption_key
    if not key or not key.strip():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="系统未配置 AI 服务商加密密钥（AI_PROVIDER_ENCRYPTION_KEY），无法加解密 API Key",
        )
    return key.strip()


def _serialize_provider(provider: AiProviderConfig, encryption_key: str) -> dict[str, Any]:
    """序列化服务商记录，api_key 字段始终返回脱敏掩码"""
    masked = "****"
    if provider.api_key:
        try:
            plain = decrypt_api_key(provider.api_key, encryption_key)
            masked = mask_api_key(plain)
        except Exception:
            masked = "****"

    return {
        "id": provider.id,
        "name": provider.name,
        "base_url": provider.base_url,
        "api_key": masked,
        "model": provider.model,
        "is_enabled": bool(provider.is_enabled),
        "priority": provider.priority,
        "timeout_seconds": provider.timeout_seconds,
        "last_error": provider.last_error,
        "last_used_at": provider.last_used_at,
        "created_at": provider.created_at,
        "updated_at": provider.updated_at,
    }


# ==================== 路由接口 ====================


@router.get("/providers", response_model=List[AiProviderItemResponse])
async def list_providers(
    _: User = Depends(get_current_admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> List[dict[str, Any]]:
    """获取所有 AI 服务商配置列表

    排序规则：priority 升序、id 升序。
    安全性：每条记录仅返回脱敏后的 api_key 掩码值，绝不暴露完整明文 Key。
    """
    encryption_key = _get_encryption_key()
    stmt = select(AiProviderConfig).order_by(AiProviderConfig.priority.asc(), AiProviderConfig.id.asc())
    result = await session.execute(stmt)
    providers = result.scalars().all()
    return [_serialize_provider(p, encryption_key) for p in providers]


@router.post("/providers", response_model=AiProviderItemResponse, status_code=status.HTTP_201_CREATED)
async def create_provider(
    payload: AiProviderCreateRequest,
    _: User = Depends(get_current_admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """新建 AI 服务商配置

    - api_key 接收明文输入，入库前使用 Fernet 算法进行对称加密。
    - base_url 自动去除末尾多余斜杠。
    - 返回新建的服务商记录，api_key 展示为脱敏掩码。
    """
    encryption_key = _get_encryption_key()
    clean_base_url = payload.base_url.strip().rstrip("/")
    plain_key = payload.api_key.strip()
    if not plain_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="API Key 不能为空")

    try:
        encrypted_key = encrypt_api_key(plain_key, encryption_key)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    provider = AiProviderConfig(
        name=payload.name.strip(),
        base_url=clean_base_url,
        api_key=encrypted_key,
        model=payload.model.strip(),
        is_enabled=payload.is_enabled,
        priority=payload.priority,
        timeout_seconds=payload.timeout_seconds,
    )
    session.add(provider)
    await session.commit()
    await session.refresh(provider)

    return _serialize_provider(provider, encryption_key)


@router.post("/providers/reorder")
async def reorder_providers(
    payload: AiProviderReorderRequest,
    _: User = Depends(get_current_admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """批量更新 AI 服务商调用顺序

    说明：
    - 按传入的 ids 列表顺序，依次将各服务商的 priority 更新为 0, 1, 2, ...
    - 容灾调度时按 priority 升序尝试，即排在前面的 ID 优先被调用。
    - 本静态路由必须注册在 /{id} 动态路由之前，避免被作为 ID 误匹配。
    """
    for index, provider_id in enumerate(payload.ids):
        result = await session.execute(
            select(AiProviderConfig).where(AiProviderConfig.id == provider_id)
        )
        provider = result.scalar_one_or_none()
        if provider:
            provider.priority = index

    await session.commit()
    return {"ok": True, "message": "服务商优先级重排完成"}


@router.post("/providers/{provider_id}/test", response_model=AiProviderTestResponse)
async def test_provider_connection(
    provider_id: int,
    _: User = Depends(get_current_admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """测试指定 AI 服务商接口连通性

    逻辑：
    - 进程内临时解密该条记录的 api_key。
    - 发送 GET {base_url}/models 请求探测连通性，设置 10 秒硬超时。
    - 请求头携带 Authorization: Bearer <解密后的Key>。
    - 返回 {ok, status, message}，若失败展示 HTTP 状态码与错误摘要，绝不泄露 API Key。
    """
    encryption_key = _get_encryption_key()
    result = await session.execute(
        select(AiProviderConfig).where(AiProviderConfig.id == provider_id)
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="服务商配置不存在")

    try:
        plain_key = decrypt_api_key(provider.api_key, encryption_key)
    except Exception as exc:
        return {
            "ok": False,
            "status": 500,
            "message": f"解密 API Key 失败，请检查系统加密配置: {str(exc)}",
        }

    url = build_openai_url(provider.base_url, "models")
    headers = {
        "Authorization": f"Bearer {plain_key}",
        "Accept": "application/json",
    }

    try:
        async with _create_async_client(timeout=10.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.is_success:
                return {"ok": True, "status": resp.status_code, "message": "服务商连接测试成功"}
            else:
                err_text = extract_response_error(resp)
                # 安全兜底：确保错误消息中不包含明文 Key
                if plain_key and plain_key in err_text:
                    err_text = err_text.replace(plain_key, "****")
                return {
                    "ok": False,
                    "status": resp.status_code,
                    "message": f"第三方接口返回异常 HTTP {resp.status_code}: {err_text}",
                }
    except httpx.TimeoutException:
        return {"ok": False, "status": 504, "message": "请求超时（10秒）"}
    except Exception as exc:
        err_msg = str(exc)
        if plain_key and plain_key in err_msg:
            err_msg = err_msg.replace(plain_key, "****")
        return {"ok": False, "status": 500, "message": f"网络或请求异常: {type(exc).__name__}: {err_msg}"}


@router.put("/providers/{provider_id}", response_model=AiProviderItemResponse)
async def update_provider(
    provider_id: int,
    payload: AiProviderUpdateRequest,
    _: User = Depends(get_current_admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """更新 AI 服务商配置

    规则：
    - api_key 规则：若提交的 api_key 为空、None 或等于当前该服务商的掩码值，表示保持原 Key 不变；
      若提交了新明文 Key，则通过 Fernet 加密后更新。
    - 记录不存在返回 404。
    """
    encryption_key = _get_encryption_key()
    result = await session.execute(
        select(AiProviderConfig).where(AiProviderConfig.id == provider_id)
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="服务商配置不存在")

    # 计算当前现存 Key 的脱敏掩码，用于判断是否"保持原 Key 不变"
    current_masked = "****"
    if provider.api_key:
        try:
            current_plain = decrypt_api_key(provider.api_key, encryption_key)
            current_masked = mask_api_key(current_plain)
        except Exception:
            current_masked = "****"

    if payload.name is not None:
        provider.name = payload.name.strip()
    if payload.base_url is not None:
        provider.base_url = payload.base_url.strip().rstrip("/")
    if payload.model is not None:
        provider.model = payload.model.strip()
    if payload.is_enabled is not None:
        provider.is_enabled = payload.is_enabled
    if payload.priority is not None:
        provider.priority = payload.priority
    if payload.timeout_seconds is not None:
        provider.timeout_seconds = payload.timeout_seconds

    # api_key 更新判断：仅在非空且不等于掩码值时才重新加密覆盖
    if payload.api_key is not None:
        new_key = payload.api_key.strip()
        if new_key and new_key != current_masked:
            try:
                provider.api_key = encrypt_api_key(new_key, encryption_key)
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc))

    await session.commit()
    await session.refresh(provider)
    return _serialize_provider(provider, encryption_key)


@router.delete("/providers/{provider_id}")
async def delete_provider(
    provider_id: int,
    _: User = Depends(get_current_admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """硬删除指定 AI 服务商配置"""
    result = await session.execute(
        select(AiProviderConfig).where(AiProviderConfig.id == provider_id)
    )
    provider = result.scalar_one_or_none()
    if not provider:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="服务商配置不存在")

    await session.delete(provider)
    await session.commit()
    return {"ok": True, "message": "服务商配置已删除"}


@router.post("/generate-listing", response_model=GenerateListingResponse)
async def generate_listing_endpoint(
    request: Request,
    _: User = Depends(get_current_admin_user),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """根据商品图片生成闲鱼发布标题、详情和分类（走容灾调度）

    入参支持两种方式（二选一）：
    1. multipart/form-data：通过 file 或 image 字段上传图片二进制文件；可选 hint 或 keywords 字段。
    2. application/json：通过 image_base64 或 image 传入 base64 编码的图片；可选 hint 或 keywords 字段。

    返回值：
    - 200: {title, description, category, provider_used}
    - 503: 未配置或未启用任何 AI 服务商
    - 502: 所有服务商均调用失败，返回每条服务商的错误详情（不含 Key）
    """
    content_type = request.headers.get("content-type", "")
    image_bytes = b""
    user_hint: Optional[str] = None

    if "multipart/form-data" in content_type:
        form = await request.form()
        uploaded_file = form.get("file") or form.get("image")
        if not uploaded_file:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="未检测到上传的图片文件（请使用 file 或 image 字段）",
            )
        if hasattr(uploaded_file, "read"):
            image_bytes = await uploaded_file.read()
        raw_hint = form.get("hint") or form.get("keywords")
        if raw_hint and isinstance(raw_hint, str):
            user_hint = raw_hint.strip()

    elif "application/json" in content_type:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="无效的 JSON 请求体")
        b64_data = body.get("image_base64") or body.get("image") or ""
        if not b64_data or not isinstance(b64_data, str):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="缺少有效的 image_base64 或 image 字段")
        if "," in b64_data:
            b64_data = b64_data.split(",", 1)[1]
        try:
            image_bytes = base64.b64decode(b64_data)
        except Exception:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="图片 Base64 解码失败")
        raw_hint = body.get("hint") or body.get("keywords")
        if raw_hint and isinstance(raw_hint, str):
            user_hint = raw_hint.strip()

    else:
        # 兼容未显式设置 Content-Type 的表单提交
        try:
            form = await request.form()
            uploaded_file = form.get("file") or form.get("image")
            if uploaded_file and hasattr(uploaded_file, "read"):
                image_bytes = await uploaded_file.read()
                raw_hint = form.get("hint") or form.get("keywords")
                if raw_hint and isinstance(raw_hint, str):
                    user_hint = raw_hint.strip()
        except Exception:
            pass

        if not image_bytes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="请通过 multipart/form-data（上传文件）或 application/json（base64）提交商品图片",
            )

    if not image_bytes:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="商品图片数据不能为空")

    service = AiProviderFailoverService(session=session)
    try:
        result = await service.generate_listing(image_bytes=image_bytes, hint=user_hint)
        return result
    except NoProviderConfiguredError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    except AllProvidersFailedError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={"message": str(exc), "errors": exc.errors},
        )
