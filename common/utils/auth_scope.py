"""
用户数据作用域与角色判断工具

功能：
1. 统一判断当前用户是否为管理员角色
2. 统一解析当前用户的数据作用域（owner_id），减少路由层重复代码

用法示例：
    from common.utils.auth_scope import resolve_owner_scope

    owner_id, is_admin = resolve_owner_scope(current_user)
    # 管理员：owner_id = None，查询全量数据
    # 其他用户：owner_id = current_user.id，仅查询本人数据

备注：
- UserRole 继承自 str，所以 UserRole.ADMIN == "ADMIN" 恒为 True，
  历史上散落的 `current_user.role == UserRole.ADMIN or current_user.role == "ADMIN"`
  属于冗余双写，这里统一收敛到枚举比较。
"""
from __future__ import annotations

from common.models.user import User, UserRole


def is_admin_user(user: User) -> bool:
    """判断当前用户是否为管理员角色。

    Args:
        user: 当前登录用户对象

    Returns:
        True 表示是管理员；False 表示普通用户。
    """
    return user.role == UserRole.ADMIN


def resolve_owner_scope(user: User) -> tuple[int | None, bool]:
    """根据当前用户角色解析数据访问作用域。

    - 管理员：不限制 owner，返回 (None, True)，可以查询所有用户数据
    - 非管理员：仅允许访问自身数据，返回 (user.id, False)

    Args:
        user: 当前登录用户对象

    Returns:
        (owner_id, is_admin) 元组
    """
    admin = is_admin_user(user)
    return (None if admin else user.id, admin)


def resolve_account_owner_scope(user: User) -> tuple[int, bool]:
    """解析闲鱼账号（xy_accounts）的数据访问作用域。

    与 resolve_owner_scope() 不同：即使是管理员，也只能访问自己的闲鱼号。
    平台上有多个 ADMIN 用户时，管理员之间默认也互相隔离，不能删/看对方的
    闲鱼号。平台用户管理等管理类功能仍使用 resolve_owner_scope()，不受影响。

    Args:
        user: 当前登录用户对象

    Returns:
        (owner_id, is_admin) 元组，其中 owner_id 恒为 user.id（永不为 None）。
    """
    return (user.id, is_admin_user(user))


__all__ = [
    "is_admin_user",
    "resolve_owner_scope",
    "resolve_account_owner_scope",
    "user_scope_condition",
    "get_account_owner_id",
]


def user_scope_condition(model, owner_id: int):
    """当前用户在“账号归属表”上的可见范围条件。

    本人归属的数据（user_id == owner_id）+ 无法归属的历史数据
    （user_id 为空，如账号已被删除、或全局共用的地址池旧数据）。
    空值行保持对所有人可见，避免历史数据因无法归属而突然消失；
    新写入的行必须带上当前用户 id，之后即按归属隔离。
    """
    from sqlalchemy import or_

    return or_(model.user_id == owner_id, model.user_id.is_(None))


async def get_account_owner_id(session, account_id: str) -> int | None:
    """按闲鱼号查归属平台用户 id（定时任务等系统侧写日志时用）。

    账号不存在（已被删除）时返回 None，调用方保持 user_id 为空。
    """
    from sqlalchemy import select

    from common.models.xy_account import XYAccount

    result = await session.execute(
        select(XYAccount.owner_id).where(XYAccount.account_id == account_id).limit(1)
    )
    return result.scalar_one_or_none()
