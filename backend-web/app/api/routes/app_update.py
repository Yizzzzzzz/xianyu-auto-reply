"""App OTA 更新接口（Flutter 安卓端自托管更新）

公开接口，无需登录：

- ``GET /api/v1/app/latest-version``：返回最新版本信息。
  ``apk_url`` 由服务端按本次请求的 Host 拼出（App 在 Tailscale 直连
  :8089 时是 ``http://100.124.135.29:8089/...``，走 :9000/:2020 代理时
  是同源地址），App 直接用它下载，不用自己拼地址。
  无已发布版本 / 版本信息有问题时返回 ``success=False`` + 中文原因，
  App 端静默处理（自动检查不打扰用户，手动检查弹 toast）。
- ``GET /api/v1/app/download/{filename}``：下载 APK 文件。
  文件不存在时返回真实的 HTTP 404（直接返回 ``Response``，而不抛
  ``HTTPException``——全局异常处理器会把抛出的 HTTP 异常统一包成
  HTTP 200 的 ApiResponse，下载器会把那段 JSON 当成 APK 存下来，
  再因校验失败报错，提示信息就误导了）。

响应统一用 ``ApiResponse``（``success/message/data``），与其它接口一致。
``data`` 字段：

.. code-block:: json

    {
      "version_name": "1.0.1",
      "version_code": 2,
      "apk_url": "http://100.124.135.29:8089/api/v1/app/download/xianyuops-1.0.1.apk",
      "apk_sha256": "<64 位十六进制>",
      "file_size": 12345678,
      "changelog": "修复了……",
      "force": false
    }

``apk_file`` 为空（只登记版本、暂无安装包）时 ``apk_url`` / ``apk_sha256``
为空字符串、``file_size`` 为 null，App 端按“无可用下载”处理。
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, Response

from app.services.app_update_service import (
    read_latest_release,
    resolve_release_file,
)
from common.schemas.common import ApiResponse

router = APIRouter(prefix="/app", tags=["App 更新"])


@router.get("/latest-version", response_model=ApiResponse)
async def get_latest_app_version(request: Request) -> ApiResponse:
    """查询 Flutter App 最新版本（公开接口，无需登录）。"""
    result = read_latest_release()
    if result.error is not None or result.release is None:
        return ApiResponse(success=False, message=result.error or "暂无已发布版本")
    release = result.release
    base = str(request.base_url).rstrip("/")
    apk_url = (
        f"{base}/api/v1/app/download/{release.apk_file}"
        if release.apk_file
        else ""
    )
    return ApiResponse(
        success=True,
        data={
            "version_name": release.version_name,
            "version_code": release.version_code,
            "apk_url": apk_url,
            "apk_sha256": release.sha256,
            "file_size": release.file_size,
            "changelog": release.changelog,
            "force": release.force,
        },
    )


@router.get("/download/{filename}")
async def download_app_apk(filename: str):
    """下载 APK 安装包（公开接口，无需登录）。"""
    path = resolve_release_file(filename)
    if path is None:
        # 直接返回 404 Response，不抛 HTTPException：全局异常处理器会把
        # 抛出的 HTTP 异常统一包成 HTTP 200 的 ApiResponse。
        return Response(
            content='{"detail": "安装包不存在"}',
            status_code=404,
            media_type="application/json",
        )
    return FileResponse(
        path,
        media_type="application/vnd.android.package-archive",
        filename=path.name,
    )
