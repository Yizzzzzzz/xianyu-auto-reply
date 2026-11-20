"""
闲鱼商品编辑服务（支持鱼小铺卖家后台与个人版）。

功能：
1. 鱼小铺账号：
   - 调用 mtop.idle.pc.backend.idleitem.editdetail 拉取平台详情转成编辑表单；
   - 调用 mtop.idle.pc.backend.idleitem.edit 提交编辑（inputJson 字符串）。
2. 个人版账号：
   - 快照拉取：调用 mtop.idle.pc.idleitem.editDetail (v1.0) 拉取完整快照；
   - 提交更新：调用 mtop.idle.pc.idleitem.edit (v1.0)，顶级字典提交；
   - 覆盖策略：提交前重新拉取快照，仅覆盖修改字段（title/price等），其余图片、发货、运费原样回填；
   - 安全措施：每次编辑保证间隔 10~15 秒（防滑块风控），平台原始错误透传至调用方。
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

from loguru import logger

from app.core.paths import STATIC_ROOT
from app.services.xianyu_direct_payload import DirectPublishError, text as _text
from app.services.xianyu_item_edit_mapper import map_edit_detail_to_form
from app.services.xianyu_item_payload_builder import build_item_payload
from common.services.xianyu_mtop import mtop_call
from common.services.xianyu_publish_service import detect_publish_account_capability

# 鱼小铺（卖家后台）接口定义
EDIT_DETAIL_API = "mtop.idle.pc.backend.idleitem.editdetail"
EDIT_API = "mtop.idle.pc.backend.idleitem.edit"
SELLER_ORIGIN = "https://seller.goofish.com"
SELLER_REFERER = "https://seller.goofish.com/?site=COMMONPRO"
SELLER_EXTRA_PARAMS = {
    "idle_site_biz_code": "COMMONPRO",
    "spm_cnt": "a21107h.42826273.0.0",
}
SELLER_EXTRA_HEADERS = {
    "idle_site_biz_code": "COMMONPRO",
    "idle_user_group_member_id": "",
}

# 个人版商品编辑接口定义（验证通过链路）
PERSONAL_EDIT_DETAIL_API = "mtop.idle.pc.idleitem.editDetail"
PERSONAL_EDIT_API = "mtop.idle.pc.idleitem.edit"
PERSONAL_ORIGIN = "https://www.goofish.com"
PERSONAL_REFERER = "https://www.goofish.com/publish"
PERSONAL_EXTRA_PARAMS = {
    "spm_cnt": "a21ybx.publish.0.0",
}

# 业务失败码白名单
_SUCCESS_CODES = {"", "0", "200", "success", "SUCCESS"}

# 防滑块风控：记录每个账号最近一次编辑时间，限制最小间隔为 12 秒
_ACCOUNT_LAST_EDIT: dict[str, float] = {}
_EDIT_THROTTLE_LOCK = asyncio.Lock()
MIN_EDIT_INTERVAL = 12.0


async def _throttle_edit(account_id: str) -> None:
    """防风控节流：同一账号编辑操作至少间隔 10~15 秒。"""
    async with _EDIT_THROTTLE_LOCK:
        last_time = _ACCOUNT_LAST_EDIT.get(account_id, 0.0)
        elapsed = time.time() - last_time
        if elapsed < MIN_EDIT_INTERVAL:
            wait = MIN_EDIT_INTERVAL - elapsed
            logger.info(
                f"【{account_id}】防风控保护：距离上次编辑仅 {elapsed:.1f}s，等待 {wait:.1f}s 后提交"
            )
            await asyncio.sleep(wait)
        _ACCOUNT_LAST_EDIT[account_id] = time.time()


def _fail(message: str, *, account_invalid: bool, cookie: str) -> dict[str, Any]:
    """构造统一的失败返回。"""
    return {
        "success": False,
        "message": message,
        "account_invalid": account_invalid,
        "cookies_str": cookie,
        "data": None,
    }


async def _call_seller_api(
    *, account_id: str, cookie: str, owner_id: int | None, api: str, data: dict[str, Any]
) -> dict[str, Any]:
    """调用鱼小铺卖家后台接口。"""
    return await mtop_call(
        account_id=account_id,
        cookies_str=cookie,
        api=api,
        version="1.0",
        data=data,
        owner_id=owner_id,
        extra_params=SELLER_EXTRA_PARAMS,
        origin=SELLER_ORIGIN,
        referer=SELLER_REFERER,
        extra_headers=SELLER_EXTRA_HEADERS,
    )


def _loggable_response(response: dict[str, Any]) -> str:
    """把 mtop 返回裁剪成可写日志的内容（剔除 Cookie 等凭据）。"""
    safe = {key: response.get(key) for key in ("success", "account_invalid", "res", "error")}
    return json.dumps(safe, ensure_ascii=False, default=str)


async def _check_account_capability(
    *, account_id: str, cookie: str, owner_id: int | None
) -> tuple[str, bool, dict[str, Any] | None]:
    """检测账号能力，返回 (cookie, is_fish_shop, failure_or_none)。"""
    capability = await detect_publish_account_capability(
        cookie, account_id=account_id, owner_id=owner_id
    )
    cookie = capability.get("cookies_str") or cookie
    if not capability.get("success"):
        return cookie, False, _fail(
            capability.get("message") or "账号发布能力检测失败，请稍后重试",
            account_invalid=bool(capability.get("account_invalid")),
            cookie=cookie,
        )
    return cookie, bool(capability.get("is_fish_shop")), None


async def _fetch_edit_detail(
    *, account_id: str, cookie: str, item_id: str, owner_id: int | None
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    """拉取鱼小铺卖家后台商品详情。"""
    response = await _call_seller_api(
        account_id=account_id,
        cookie=cookie,
        owner_id=owner_id,
        api=EDIT_DETAIL_API,
        data={"itemId": item_id},
    )
    cookie = response.get("cookies_str") or cookie
    if not response.get("success"):
        logger.error(
            f"闲鱼鱼小铺编辑详情失败完整返回: account_id={account_id}, item_id={item_id}, "
            f"response={_loggable_response(response)}"
        )
        return (
            cookie,
            None,
            _fail(
                f"获取闲鱼商品详情失败：{response.get('error') or '未知错误'}",
                account_invalid=bool(response.get("account_invalid")),
                cookie=cookie,
            ),
        )
    res = response.get("res") if isinstance(response.get("res"), dict) else {}
    detail = res.get("data") if isinstance(res.get("data"), dict) else None
    if not detail or not _text(detail.get("itemId")):
        return cookie, None, _fail("闲鱼未返回商品详情，无法编辑", account_invalid=False, cookie=cookie)
    return cookie, detail, None


async def _fetch_personal_edit_detail(
    *, account_id: str, cookie: str, item_id: str, owner_id: int | None
) -> tuple[str, dict[str, Any] | None, dict[str, Any] | None]:
    """拉取个人版商品详情快照 (mtop.idle.pc.idleitem.editDetail)。"""
    response = await mtop_call(
        account_id=account_id,
        cookies_str=cookie,
        api=PERSONAL_EDIT_DETAIL_API,
        version="1.0",
        data={"itemId": str(item_id)},
        owner_id=owner_id,
        origin=PERSONAL_ORIGIN,
        referer=PERSONAL_REFERER,
        extra_params=PERSONAL_EXTRA_PARAMS,
    )
    cookie = response.get("cookies_str") or cookie
    if not response.get("success"):
        logger.error(
            f"闲鱼个人版编辑详情获取失败: account_id={account_id}, item_id={item_id}, "
            f"response={_loggable_response(response)}"
        )
        return (
            cookie,
            None,
            _fail(
                f"获取闲鱼商品详情快照失败：{response.get('error') or '未知错误'}",
                account_invalid=bool(response.get("account_invalid")),
                cookie=cookie,
            ),
        )
    res = response.get("res") if isinstance(response.get("res"), dict) else {}
    ret = res.get("ret") or []
    ret_str = ret[0] if ret else ""
    if ret_str and "SUCCESS" not in ret_str:
        return cookie, None, _fail(f"获取商品详情快照失败：{ret_str}", account_invalid=False, cookie=cookie)

    data = res.get("data")
    if not isinstance(data, dict):
        return cookie, None, _fail("闲鱼返回的商品快照为空，无法编辑", account_invalid=False, cookie=cookie)
    return cookie, data, None


def _business_failure_message(res: dict[str, Any]) -> str:
    """从 edit 接口返回识别业务失败提示。"""
    data = res.get("data") if isinstance(res.get("data"), dict) else {}
    if not data:
        return ""
    message = ""
    for key in ("msg", "message", "errorMsg", "errMsg", "resultMsg", "tips"):
        message = _text(data.get(key))
        if message:
            break
    for key in ("success", "isSuccess"):
        if key in data and data.get(key) in (False, "false", "False"):
            return message or "闲鱼返回编辑失败"
    code = _text(data.get("code")) or _text(data.get("errorCode"))
    if code and code not in _SUCCESS_CODES:
        return message or f"闲鱼返回编辑失败（code={code}）"
    return ""


async def fetch_seller_item_edit_detail(
    *, account_id: str, cookie: str, item_id: str, owner_id: int | None = None
) -> dict[str, Any]:
    """拉取商品详情并转成编辑表单数据（根据账号类型自动分流）。"""
    item_id = _text(item_id)
    if not item_id:
        return _fail("缺少闲鱼商品ID，无法编辑", account_invalid=False, cookie=cookie)

    cookie, is_fish_shop, failure = await _check_account_capability(
        account_id=account_id, cookie=cookie, owner_id=owner_id
    )
    if failure:
        return failure

    if is_fish_shop:
        cookie, detail, failure = await _fetch_edit_detail(
            account_id=account_id, cookie=cookie, item_id=item_id, owner_id=owner_id
        )
    else:
        cookie, detail, failure = await _fetch_personal_edit_detail(
            account_id=account_id, cookie=cookie, item_id=item_id, owner_id=owner_id
        )

    if failure:
        return failure

    form = map_edit_detail_to_form(detail or {})
    return {
        "success": True,
        "message": "获取商品详情成功",
        "account_invalid": False,
        "cookies_str": cookie,
        "data": {"form": form},
    }


async def edit_personal_item(
    *,
    account_id: str,
    cookie: str,
    item_id: str,
    title: str | None = None,
    price: float | None = None,
    original_price: float | None = None,
    description: str | None = None,
    owner_id: int | None = None,
) -> dict[str, Any]:
    """提交个人号商品编辑。

    链路：
    1. 提交前防滑块节流（至少间隔 10~15 秒）；
    2. 拉取 mtop.idle.pc.idleitem.editDetail 获得完整快照；
    3. 仅覆盖修改的字段（title、priceInCent、desc等），其他快照字段原样保留；
    4. 提交到 mtop.idle.pc.idleitem.edit (v1.0)，顶级字典提交。
    """
    item_id = _text(item_id)
    if not item_id:
        return _fail("缺少闲鱼商品ID，无法编辑", account_invalid=False, cookie=cookie)

    # 1. 防风控节流
    await _throttle_edit(account_id)

    # 2. 重新拉取平台详情快照
    cookie, snapshot, failure = await _fetch_personal_edit_detail(
        account_id=account_id, cookie=cookie, item_id=item_id, owner_id=owner_id
    )
    if failure:
        return failure

    payload = dict(snapshot or {})

    # 3. 逐项回填覆盖修改字段
    if title is not None:
        new_title = _text(title)
        if not new_title:
            return _fail("商品标题不能为空", account_invalid=False, cookie=cookie)
        text_dto = payload.setdefault("itemTextDTO", {})
        if isinstance(text_dto, dict):
            text_dto["title"] = new_title
            text_dto["titleDescSeparate"] = "true"

    if description is not None:
        text_dto = payload.setdefault("itemTextDTO", {})
        if isinstance(text_dto, dict):
            text_dto["desc"] = str(description)

    if price is not None:
        try:
            cents = str(round(float(price) * 100))
            if int(cents) <= 0:
                return _fail("售价必须大于0", account_invalid=False, cookie=cookie)
        except (TypeError, ValueError):
            return _fail("售价格式不正确", account_invalid=False, cookie=cookie)
        price_dto = payload.setdefault("itemPriceDTO", {})
        if isinstance(price_dto, dict):
            price_dto["priceInCent"] = cents

    if original_price is not None:
        try:
            orig_cents = str(round(float(original_price) * 100))
            price_dto = payload.setdefault("itemPriceDTO", {})
            if isinstance(price_dto, dict):
                price_dto["origPriceInCent"] = orig_cents
        except (TypeError, ValueError):
            pass

    payload["itemId"] = item_id

    # 4. 提交编辑接口
    logger.info(
        f"闲鱼个人号商品编辑提交请求: account_id={account_id}, item_id={item_id}, "
        f"title={title}, price={price}"
    )
    response = await mtop_call(
        account_id=account_id,
        cookies_str=cookie,
        api=PERSONAL_EDIT_API,
        version="1.0",
        data=payload,
        owner_id=owner_id,
        origin=PERSONAL_ORIGIN,
        referer=PERSONAL_REFERER,
        extra_params=PERSONAL_EXTRA_PARAMS,
    )
    cookie = response.get("cookies_str") or cookie
    logger.info(
        f"闲鱼个人号商品编辑接口完整返回: account_id={account_id}, item_id={item_id}, "
        f"response={_loggable_response(response)}"
    )

    if not response.get("success"):
        return _fail(
            f"闲鱼接口编辑失败：{response.get('error') or '未知错误'}",
            account_invalid=bool(response.get("account_invalid")),
            cookie=cookie,
        )

    res = response.get("res") if isinstance(response.get("res"), dict) else {}
    ret = res.get("ret") or []
    ret_str = ret[0] if ret else ""
    if ret_str and "SUCCESS" not in ret_str:
        return _fail(f"闲鱼接口编辑失败：{ret_str}", account_invalid=False, cookie=cookie)

    biz_data = res.get("data")
    if isinstance(biz_data, dict):
        code = str(biz_data.get("code") or biz_data.get("errorCode") or "")
        if code and code not in _SUCCESS_CODES:
            msg = biz_data.get("msg") or biz_data.get("errorMsg") or f"code={code}"
            return _fail(f"闲鱼接口编辑失败：{msg}", account_invalid=False, cookie=cookie)
        if biz_data.get("success") is False:
            msg = biz_data.get("msg") or biz_data.get("errorMsg") or "业务处理失败"
            return _fail(f"闲鱼接口编辑失败：{msg}", account_invalid=False, cookie=cookie)

    return {
        "success": True,
        "message": "商品编辑成功",
        "account_invalid": False,
        "cookies_str": cookie,
        "data": None,
    }


async def edit_seller_item(
    *,
    account_id: str,
    cookie: str,
    item_id: str,
    item_data: dict[str, Any],
    owner_id: int | None = None,
    static_root: str | Path | None = None,
) -> dict[str, Any]:
    """提交商品编辑，按账号类型自动分流（鱼小铺走全量构造器，个人号走快照回填）。"""
    item_id = _text(item_id)
    if not item_id:
        return _fail("缺少闲鱼商品ID，无法编辑", account_invalid=False, cookie=cookie)

    cookie, is_fish_shop, failure = await _check_account_capability(
        account_id=account_id, cookie=cookie, owner_id=owner_id
    )
    if failure:
        return failure

    # 个人号分流
    if not is_fish_shop:
        return await edit_personal_item(
            account_id=account_id,
            cookie=cookie,
            item_id=item_id,
            title=item_data.get("title"),
            price=item_data.get("price"),
            original_price=item_data.get("original_price"),
            description=item_data.get("description"),
            owner_id=owner_id,
        )

    # 鱼小铺账号流程
    await _throttle_edit(account_id)
    cookie, snapshot, failure = await _fetch_edit_detail(
        account_id=account_id, cookie=cookie, item_id=item_id, owner_id=owner_id
    )
    if failure:
        return failure

    try:
        payload, cookie = await build_item_payload(
            item_data,
            cookie,
            account_id,
            owner_id,
            static_root=static_root or STATIC_ROOT,
            snapshot=snapshot,
        )
    except DirectPublishError as exc:
        return _fail(str(exc), account_invalid=exc.account_invalid, cookie=cookie)

    payload["itemId"] = item_id
    cat_dto = payload.get("itemCatDTO")
    if isinstance(cat_dto, dict) and not _text(cat_dto.get("leafId")):
        cat_dto.pop("leafId", None)

    response = await _call_seller_api(
        account_id=account_id,
        cookie=cookie,
        owner_id=owner_id,
        api=EDIT_API,
        data={"inputJson": json.dumps(payload, ensure_ascii=False, separators=(",", ":"))},
    )
    cookie = response.get("cookies_str") or cookie

    logger.info(
        f"闲鱼鱼小铺编辑接口完整返回: account_id={account_id}, item_id={item_id}, "
        f"response={_loggable_response(response)}"
    )

    if not response.get("success"):
        return _fail(
            f"闲鱼接口编辑失败：{response.get('error') or '未知错误'}",
            account_invalid=bool(response.get("account_invalid")),
            cookie=cookie,
        )

    res = response.get("res") if isinstance(response.get("res"), dict) else {}
    business_failure = _business_failure_message(res)
    if business_failure:
        return _fail(f"闲鱼接口编辑失败：{business_failure}", account_invalid=False, cookie=cookie)

    return {
        "success": True,
        "message": "商品编辑成功",
        "account_invalid": False,
        "cookies_str": cookie,
        "data": None,
    }


__all__ = ["edit_seller_item", "fetch_seller_item_edit_detail", "edit_personal_item"]
