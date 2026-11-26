"""确认收货消息API路由

提供确认收货消息的增删改查接口
"""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db_session
from common.models.confirm_receipt_message import ConfirmReceiptMessage
from common.models.user import User
from common.models.xy_account import XYAccount
from common.schemas.common import ApiResponse
from common.utils.auth_scope import resolve_account_owner_scope, user_scope_condition
from common.utils.local_image_upload import ImageUploadError, save_uploaded_image
from common.utils.time_utils import get_beijing_now_naive

router = APIRouter(tags=["confirm-receipt-messages"])


async def _get_owned_account(
    db: AsyncSession, account_id: str, current_user: User
) -> XYAccount | None:
    """按归属查询账号：即使是管理员也只限本人账号（账号间隔离）；不存在或非本人均返回 None。

    ConfirmReceiptMessage 表原先只有 account_id、无 user_id，故先通过账号解析归属再校验；
    现写入时同时记录 user_id，查询再按 user_id 过滤，双重隔离。
    """
    owner_id, _ = resolve_account_owner_scope(current_user)
    conditions = [XYAccount.account_id == account_id, XYAccount.owner_id == owner_id]
    result = await db.execute(select(XYAccount).where(*conditions))
    return result.scalars().first()

# 图片保存目录 - 使用统一的静态文件根目录（兼容Docker共享卷）
from app.core.paths import STATIC_ROOT
UPLOAD_DIR = str(STATIC_ROOT / "uploads" / "confirm_receipt")


class ConfirmReceiptMessageResponse(BaseModel):
    """确认收货消息响应"""
    enabled: bool = False
    message_content: str = ""
    message_image: str = ""


class ConfirmReceiptMessageUpdate(BaseModel):
    """确认收货消息更新请求"""
    enabled: bool = False
    message_content: str = ""
    message_image: str = ""


@router.get("/{account_id}", response_model=ConfirmReceiptMessageResponse)
async def get_confirm_receipt_message(
    account_id: str,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """获取账号的确认收货消息配置"""
    if await _get_owned_account(db, account_id, current_user) is None:
        raise HTTPException(status_code=404, detail="账号不存在或无权访问")
    owner_id, _ = resolve_account_owner_scope(current_user)
    result = await db.execute(
        select(ConfirmReceiptMessage).where(
            ConfirmReceiptMessage.account_id == account_id,
            user_scope_condition(ConfirmReceiptMessage, owner_id),
        )
    )
    message = result.scalar_one_or_none()
    
    if not message:
        return ConfirmReceiptMessageResponse()
    
    return ConfirmReceiptMessageResponse(
        enabled=message.enabled,
        message_content=message.message_content or "",
        message_image=message.message_image or "",
    )


@router.put("/{account_id}")
async def update_confirm_receipt_message(
    account_id: str,
    data: ConfirmReceiptMessageUpdate,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """更新账号的确认收货消息配置"""
    if await _get_owned_account(db, account_id, current_user) is None:
        raise HTTPException(status_code=404, detail="账号不存在或无权访问")
    owner_id, _ = resolve_account_owner_scope(current_user)
    # 按账号查找（不加 user 过滤，避免同一账号出现重复行）；写时认领归属
    result = await db.execute(
        select(ConfirmReceiptMessage).where(ConfirmReceiptMessage.account_id == account_id)
    )
    message = result.scalar_one_or_none()

    if message:
        # 更新现有记录
        if message.user_id != owner_id:
            message.user_id = owner_id
        message.enabled = data.enabled
        message.message_content = data.message_content
        message.message_image = data.message_image
        message.updated_at = get_beijing_now_naive()
    else:
        # 创建新记录
        message = ConfirmReceiptMessage(
            account_id=account_id,
            user_id=owner_id,
            enabled=data.enabled,
            message_content=data.message_content,
            message_image=data.message_image,
        )
        db.add(message)
    
    await db.commit()

    return ApiResponse(success=True, message="保存成功")


@router.post("/{account_id}/upload-image")
async def upload_confirm_receipt_image(
    account_id: str,
    image: UploadFile = File(...),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user),
):
    """上传确认收货消息图片"""
    if await _get_owned_account(db, account_id, current_user) is None:
        raise HTTPException(status_code=404, detail="账号不存在或无权访问")
    try:
        _, filename, _ = await save_uploaded_image(
            image,
            UPLOAD_DIR,
            filename_prefix=account_id,
            short_uuid=True,
        )
    except ImageUploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    # 返回前端可访问的静态资源URL
    image_url = f"/static/uploads/confirm_receipt/{filename}"

    return {"success": True, "image_url": image_url}
