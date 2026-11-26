"""
Token 用量看板路由

GET /api/v1/stats/token-usage
按用户、按天汇总过去 N 天的 AI token 用量。

权限：
- 普通用户只能看自己的用量。
- ADMIN 可以看全部用户的用量（可传 user_id 只看某一个用户）。
- 注意：这里用 resolve_owner_scope()，ADMIN 返回 owner_id=None 表示看全部；
  这是看板本身的聚合视角，不是闲鱼账号的归属隔离。闲鱼账号（xy_accounts）
  仍用 resolve_account_owner_scope()，ADMIN 之间也互相隔离，两者不要混用。
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from common.models import AITokenUsage, User
from common.schemas.common import ApiResponse
from common.utils.auth_scope import resolve_owner_scope

router = APIRouter(prefix="/stats", tags=["Token用量统计"])


@router.get("/token-usage", response_model=ApiResponse)
async def get_token_usage(
    days: int = Query(7, ge=1, le=90, description="过去几天，默认7天，范围1-90"),
    user_id: Optional[int] = Query(None, description="只看某个平台用户（仅ADMIN可用）"),
    current_user: User = Depends(deps.get_current_active_user),
    db: AsyncSession = Depends(deps.get_db_session),
) -> ApiResponse:
    """按用户、按天汇总过去 N 天的 AI token 用量。

    返回结构：
    {
      "days": 7,
      "start_date": "2026-11-20",
      "end_date": "2026-11-27",
      "by_user": [
        {
          "user_id": 1,
          "username": "admin",
          "total_tokens": 12345,
          "prompt_tokens": 8000,
          "completion_tokens": 4345,
          "calls": 42,
          "by_day": [
            {"date": "2026-11-27", "prompt_tokens": 100, "completion_tokens": 50,
             "total_tokens": 150, "calls": 2},
            ...
          ]
        },
        ...
      ],
      "totals": {"prompt_tokens": ..., "completion_tokens": ..., "total_tokens": ..., "calls": ...}
    }

    说明：
    - 用量只从 AI 调用成功、服务商返回了 usage 字段的记录里统计；
      服务商没返回 usage 的调用不会计入。
    - by_day 按日期升序，包含过去 N 天每天一条（当天没有调用则 token 为 0）。
    """
    owner_id, is_admin = resolve_owner_scope(current_user)

    # 非 ADMIN 只能看自己；ADMIN 默认看全部，传了 user_id 则只看该用户
    if not is_admin:
        target_owner_id: int | None = current_user.id
    else:
        target_owner_id = user_id

    now = datetime.now(timezone.utc)
    end_date = now.date()
    start_date = end_date - timedelta(days=days - 1)
    start_dt = datetime(start_date.year, start_date.month, start_date.day, tzinfo=timezone.utc)

    # 按用户、按天聚合
    day_expr = func.date(AITokenUsage.created_at)
    stmt = (
        select(
            AITokenUsage.owner_id,
            day_expr.label("day"),
            func.coalesce(func.sum(AITokenUsage.prompt_tokens), 0).label("prompt_tokens"),
            func.coalesce(func.sum(AITokenUsage.completion_tokens), 0).label("completion_tokens"),
            func.coalesce(func.sum(AITokenUsage.total_tokens), 0).label("total_tokens"),
            func.count(AITokenUsage.id).label("calls"),
        )
        .where(AITokenUsage.created_at >= start_dt)
        .group_by(AITokenUsage.owner_id, day_expr)
        .order_by(AITokenUsage.owner_id, day_expr)
    )
    if target_owner_id is not None:
        stmt = stmt.where(AITokenUsage.owner_id == target_owner_id)
    rows = (await db.execute(stmt)).all()

    # 用户名 lookup
    user_stmt = select(User.id, User.username)
    if target_owner_id is not None:
        user_stmt = user_stmt.where(User.id == target_owner_id)
    user_rows = (await db.execute(user_stmt)).all()
    username_map: dict[int, str] = {uid: name for uid, name in user_rows}

    # 组织成按用户、按天的结构（每天一条，无调用则 0）
    day_list = [start_date + timedelta(days=i) for i in range(days)]
    day_strs = [d.isoformat() for d in day_list]

    per_user: dict[int, dict[str, Any]] = {}
    for r in rows:
        uid = int(r.owner_id)
        entry = per_user.setdefault(
            uid,
            {
                "user_id": uid,
                "username": username_map.get(uid, str(uid)),
                "total_tokens": 0,
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "calls": 0,
                "by_day_map": {},
            },
        )
        d = r.day.isoformat() if hasattr(r.day, "isoformat") else str(r.day)
        entry["by_day_map"][d] = {
            "date": d,
            "prompt_tokens": int(r.prompt_tokens or 0),
            "completion_tokens": int(r.completion_tokens or 0),
            "total_tokens": int(r.total_tokens or 0),
            "calls": int(r.calls or 0),
        }
        entry["prompt_tokens"] += int(r.prompt_tokens or 0)
        entry["completion_tokens"] += int(r.completion_tokens or 0)
        entry["total_tokens"] += int(r.total_tokens or 0)
        entry["calls"] += int(r.calls or 0)

    by_user: list[dict[str, Any]] = []
    totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
    for uid in sorted(per_user):
        entry = per_user[uid]
        by_day = [
            entry["by_day_map"].get(
                ds,
                {"date": ds, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0},
            )
            for ds in day_strs
        ]
        by_user.append(
            {
                "user_id": entry["user_id"],
                "username": entry["username"],
                "total_tokens": entry["total_tokens"],
                "prompt_tokens": entry["prompt_tokens"],
                "completion_tokens": entry["completion_tokens"],
                "calls": entry["calls"],
                "by_day": by_day,
            }
        )
        totals["prompt_tokens"] += entry["prompt_tokens"]
        totals["completion_tokens"] += entry["completion_tokens"]
        totals["total_tokens"] += entry["total_tokens"]
        totals["calls"] += entry["calls"]

    return ApiResponse(
        success=True,
        data={
            "days": days,
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "by_user": by_user,
            "totals": totals,
        },
    )
