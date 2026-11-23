"""App OTA 更新服务（Flutter 安卓端自托管更新）

版本信息存在服务器上的 ``releases/latest.json``，发新版时只要把新 APK
放进 ``releases/`` 目录并改这个 JSON，不用改代码、不用重启：

.. code-block:: json

    {
      "version_name": "1.0.1",
      "version_code": 2,
      "apk_file": "xianyuops-1.0.1.apk",
      "sha256": "<APK 文件的 SHA-256（64 位十六进制）>",
      "changelog": "修复了……",
      "force": false
    }

安全规则：

- ``apk_file`` 非空时，文件必须真实存在于 ``releases/`` 目录下，
  且 ``sha256`` 必须是 64 位十六进制，否则不发布该版本（返回错误，
  App 端永远收不到一个下不到或验不过的更新）。
- ``file_size`` 不采信 JSON 里的值，始终取磁盘上 APK 的真实大小。
- 下载文件名只允许纯 ``.apk`` 文件名，拒绝任何带路径分隔符的名字，
  防止目录穿越。

注意：不要和已有的 ``/version`` 路由（平台自身版本检测，走
``xy-update.zhinianboke.com`` 远程 version.json）混淆，那是桌面启动器 /
Web 平台的更新通道；这里是 Flutter App 专用的版本查询接口。
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

LATEST_FILE_NAME = "latest.json"
RELEASES_DIR_NAME = "releases"
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def get_releases_dir() -> Path:
    """返回存放 APK 与 latest.json 的目录。

    优先读环境变量 ``APP_RELEASES_DIR``；默认取项目根目录下的
    ``releases/``（本文件在 ``backend-web/app/services/`` 下，
    ``parents[3]`` 即项目根目录）。
    """
    override = os.environ.get("APP_RELEASES_DIR", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / RELEASES_DIR_NAME


@dataclass
class LatestRelease:
    """latest.json 解析后的最新版本信息。"""

    version_name: str
    version_code: int
    apk_file: str
    sha256: str
    changelog: str
    force: bool
    file_size: int | None = None


@dataclass
class LatestReleaseResult:
    """读取结果：成功时 ``release`` 有值，失败时 ``error`` 有值。"""

    release: LatestRelease | None = None
    error: str | None = None


def resolve_release_file(filename: str) -> Path | None:
    """把下载文件名解析为 releases/ 下的真实文件路径。

    只允许纯 ``.apk`` 文件名；带路径分隔符、非 ``.apk`` 后缀、
    文件不存在或不在 releases/ 目录下的，一律返回 None。
    """
    name = (filename or "").strip()
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        return None
    if not name.lower().endswith(".apk"):
        return None
    releases_dir = get_releases_dir()
    try:
        resolved = (releases_dir / name).resolve()
        base = releases_dir.resolve()
    except Exception:
        return None
    if resolved.parent != base:
        return None
    return resolved if resolved.is_file() else None


def read_latest_release() -> LatestReleaseResult:
    """读取并校验 ``releases/latest.json``。

    - 文件不存在：返回 error="暂无已发布版本"（初始状态，未发过 APK）。
    - ``apk_file`` 为空：表示只登记了版本号、暂无可下载安装包，
      同样正常返回，App 端按无可用下载处理。
    - ``apk_file`` 非空但文件缺失、或 ``sha256`` 不是 64 位十六进制：
      返回 error，坚决不发布一个下不到或验不过的版本。
    """
    releases_dir = get_releases_dir()
    latest_path = releases_dir / LATEST_FILE_NAME
    if not latest_path.is_file():
        return LatestReleaseResult(error="暂无已发布版本")
    try:
        raw = json.loads(latest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning(f"[App 更新] 读取 {latest_path} 失败: {exc}")
        return LatestReleaseResult(error="版本信息文件格式错误")
    if not isinstance(raw, dict):
        return LatestReleaseResult(error="版本信息文件格式错误")

    version_name = raw.get("version_name")
    version_code = raw.get("version_code")
    if not isinstance(version_name, str) or not version_name.strip():
        return LatestReleaseResult(error="版本信息缺少 version_name")
    if isinstance(version_code, bool) or not isinstance(version_code, int):
        return LatestReleaseResult(error="版本信息缺少 version_code")
    if version_code <= 0:
        return LatestReleaseResult(error="version_code 必须大于 0")

    apk_file = raw.get("apk_file", "")
    if not isinstance(apk_file, str):
        return LatestReleaseResult(error="apk_file 必须是字符串")
    apk_file = apk_file.strip()
    sha256 = raw.get("sha256", "")
    if not isinstance(sha256, str):
        return LatestReleaseResult(error="sha256 必须是字符串")
    sha256 = sha256.strip().lower()
    changelog = raw.get("changelog", "")
    if not isinstance(changelog, str):
        changelog = str(changelog)
    force = raw.get("force", False)
    if not isinstance(force, bool):
        force = bool(force)

    file_size: int | None = None
    if apk_file:
        apk_path = resolve_release_file(apk_file)
        if apk_path is None:
            logger.warning(f"[App 更新] 安装包文件不存在: {apk_file}")
            return LatestReleaseResult(error="安装包文件尚未上传")
        if not _SHA256_RE.match(sha256):
            logger.warning("[App 更新] 安装包缺少有效的 SHA-256 校验值")
            return LatestReleaseResult(error="安装包缺少有效的 SHA-256 校验值")
        file_size = apk_path.stat().st_size

    return LatestReleaseResult(
        release=LatestRelease(
            version_name=version_name.strip(),
            version_code=version_code,
            apk_file=apk_file,
            sha256=sha256,
            changelog=changelog,
            force=force,
            file_size=file_size,
        )
    )
