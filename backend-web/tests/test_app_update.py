"""
App OTA 更新接口测试集 (App Update Tests)

测试目标：
1. Service 层：latest.json 缺失 / 格式错误 / 字段缺失 / 安装包缺失 /
   SHA-256 无效时均返回明确的中文错误，绝不发布一个下不到或验不过的版本。
2. Service 层：合法版本解析正确，file_size 取磁盘真实大小。
3. 下载文件名校验：拒绝目录穿越、非 .apk 后缀、不存在的文件。
4. Route 层：GET /app/latest-version 返回 ApiResponse 结构与字段契约
   （version_name / version_code / apk_url / apk_sha256 / file_size /
   changelog / force），apk_url 由服务端按请求 Host 拼出；
   GET /app/download/{filename} 正常下载、缺失时 404。

运行方式（与其它后端测试一致，需服务器 conda 环境）：
    cd backend-web && PYTHONPATH=.:.. python -m unittest tests.test_app_update -v
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.app_update import router as app_update_router
from app.services.app_update_service import (
    get_releases_dir,
    read_latest_release,
    resolve_release_file,
)

_VALID_SHA256 = "a" * 64


class _ReleasesDirMixin:
    """每个测试用例独占一个临时 releases/ 目录。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.releases_dir = Path(self._tmp.name) / "releases"
        self.releases_dir.mkdir(parents=True)
        self._old_env = os.environ.get("APP_RELEASES_DIR")
        os.environ["APP_RELEASES_DIR"] = str(self.releases_dir)

    def tearDown(self) -> None:
        if self._old_env is None:
            os.environ.pop("APP_RELEASES_DIR", None)
        else:
            os.environ["APP_RELEASES_DIR"] = self._old_env
        self._tmp.cleanup()

    def _write_latest(self, data) -> None:
        if isinstance(data, str):
            content = data
        else:
            content = json.dumps(data, ensure_ascii=False)
        (self.releases_dir / "latest.json").write_text(content, encoding="utf-8")

    def _write_apk(self, name: str, content: bytes = b"fake-apk-bytes") -> Path:
        path = self.releases_dir / name
        path.write_bytes(content)
        return path


class TestReadLatestRelease(_ReleasesDirMixin, unittest.TestCase):
    """latest.json 读取与校验。"""

    def test_missing_latest_json(self) -> None:
        result = read_latest_release()
        self.assertIsNone(result.release)
        self.assertEqual(result.error, "暂无已发布版本")

    def test_invalid_json(self) -> None:
        self._write_latest("{不是 json")
        result = read_latest_release()
        self.assertIsNone(result.release)
        self.assertEqual(result.error, "版本信息文件格式错误")

    def test_non_dict_json(self) -> None:
        self._write_latest("[1, 2, 3]")
        result = read_latest_release()
        self.assertIsNone(result.release)
        self.assertEqual(result.error, "版本信息文件格式错误")

    def test_missing_version_name(self) -> None:
        self._write_latest({"version_code": 2})
        result = read_latest_release()
        self.assertIsNone(result.release)
        self.assertIn("version_name", result.error or "")

    def test_missing_version_code(self) -> None:
        self._write_latest({"version_name": "1.0.1"})
        result = read_latest_release()
        self.assertIsNone(result.release)
        self.assertIn("version_code", result.error or "")

    def test_version_code_must_be_positive_int(self) -> None:
        for bad in (0, -1, "2", True):
            with self.subTest(bad=bad):
                self._write_latest(
                    {"version_name": "1.0.1", "version_code": bad}
                )
                result = read_latest_release()
                self.assertIsNone(result.release)
                self.assertIsNotNone(result.error)

    def test_valid_release_with_apk(self) -> None:
        apk = self._write_apk("xianyuops-1.0.1.apk", b"0123456789abcdef")
        self._write_latest(
            {
                "version_name": "1.0.1",
                "version_code": 2,
                "apk_file": "xianyuops-1.0.1.apk",
                "sha256": _VALID_SHA256,
                "changelog": "修复了若干问题",
                "force": True,
            }
        )
        result = read_latest_release()
        self.assertIsNone(result.error)
        self.assertIsNotNone(result.release)
        assert result.release is not None
        self.assertEqual(result.release.version_name, "1.0.1")
        self.assertEqual(result.release.version_code, 2)
        self.assertEqual(result.release.apk_file, "xianyuops-1.0.1.apk")
        self.assertEqual(result.release.sha256, _VALID_SHA256)
        self.assertEqual(result.release.changelog, "修复了若干问题")
        self.assertTrue(result.release.force)
        # file_size 取磁盘真实大小，不采信 JSON
        self.assertEqual(result.release.file_size, apk.stat().st_size)

    def test_apk_file_empty_means_no_download(self) -> None:
        self._write_latest(
            {
                "version_name": "1.0.0",
                "version_code": 1,
                "apk_file": "",
                "sha256": "",
                "changelog": "",
                "force": False,
            }
        )
        result = read_latest_release()
        self.assertIsNone(result.error)
        assert result.release is not None
        self.assertEqual(result.release.apk_file, "")
        self.assertIsNone(result.release.file_size)

    def test_apk_file_missing(self) -> None:
        self._write_latest(
            {
                "version_name": "1.0.1",
                "version_code": 2,
                "apk_file": "xianyuops-1.0.1.apk",
                "sha256": _VALID_SHA256,
                "changelog": "",
                "force": False,
            }
        )
        result = read_latest_release()
        self.assertIsNone(result.release)
        self.assertEqual(result.error, "安装包文件尚未上传")

    def test_sha256_invalid(self) -> None:
        self._write_apk("xianyuops-1.0.1.apk")
        for bad_sha in ("", "abc123", "g" * 64, "a" * 63):
            with self.subTest(bad_sha=bad_sha):
                self._write_latest(
                    {
                        "version_name": "1.0.1",
                        "version_code": 2,
                        "apk_file": "xianyuops-1.0.1.apk",
                        "sha256": bad_sha,
                        "changelog": "",
                        "force": False,
                    }
                )
                result = read_latest_release()
                self.assertIsNone(result.release)
                self.assertEqual(result.error, "安装包缺少有效的 SHA-256 校验值")


class TestResolveReleaseFile(_ReleasesDirMixin, unittest.TestCase):
    """下载文件名校验：只允许 releases/ 下真实存在的 .apk。"""

    def test_existing_apk(self) -> None:
        apk = self._write_apk("xianyuops-1.0.1.apk")
        self.assertEqual(resolve_release_file("xianyuops-1.0.1.apk"), apk.resolve())

    def test_missing_apk(self) -> None:
        self.assertIsNone(resolve_release_file("xianyuops-9.9.9.apk"))

    def test_path_traversal_rejected(self) -> None:
        for bad in ("../secret.apk", "..\\secret.apk", "sub/x.apk", "/etc/x.apk"):
            with self.subTest(bad=bad):
                self.assertIsNone(resolve_release_file(bad))

    def test_non_apk_rejected(self) -> None:
        self._write_apk("notes.txt", b"hi")
        self.assertIsNone(resolve_release_file("notes.txt"))
        self.assertIsNone(resolve_release_file("latest.json"))
        self.assertIsNone(resolve_release_file(""))
        self.assertIsNone(resolve_release_file(".."))


class TestAppUpdateRoutes(_ReleasesDirMixin, unittest.TestCase):
    """路由层：字段契约与下载行为。"""

    def setUp(self) -> None:
        super().setUp()
        app = FastAPI()
        app.include_router(app_update_router, prefix="/api/v1")
        self.client = TestClient(app, raise_server_exceptions=False)

    def test_latest_version_success(self) -> None:
        self._write_apk("xianyuops-1.0.1.apk", b"0123456789abcdef")
        self._write_latest(
            {
                "version_name": "1.0.1",
                "version_code": 2,
                "apk_file": "xianyuops-1.0.1.apk",
                "sha256": _VALID_SHA256,
                "changelog": "修复了若干问题",
                "force": False,
            }
        )
        resp = self.client.get("/api/v1/app/latest-version")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["success"])
        data = body["data"]
        self.assertEqual(data["version_name"], "1.0.1")
        self.assertEqual(data["version_code"], 2)
        self.assertEqual(
            data["apk_url"],
            "http://testserver/api/v1/app/download/xianyuops-1.0.1.apk",
        )
        self.assertEqual(data["apk_sha256"], _VALID_SHA256)
        self.assertEqual(data["file_size"], 16)
        self.assertEqual(data["changelog"], "修复了若干问题")
        self.assertFalse(data["force"])

    def test_latest_version_no_release(self) -> None:
        resp = self.client.get("/api/v1/app/latest-version")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["message"], "暂无已发布版本")
        self.assertIsNone(body["data"])

    def test_latest_version_no_apk_yet(self) -> None:
        self._write_latest(
            {
                "version_name": "1.0.0",
                "version_code": 1,
                "apk_file": "",
                "sha256": "",
                "changelog": "",
                "force": False,
            }
        )
        resp = self.client.get("/api/v1/app/latest-version")
        body = resp.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["data"]["apk_url"], "")
        self.assertEqual(body["data"]["apk_sha256"], "")
        self.assertIsNone(body["data"]["file_size"])

    def test_download_success(self) -> None:
        content = b"fake-apk-bytes"
        self._write_apk("xianyuops-1.0.1.apk", content)
        resp = self.client.get("/api/v1/app/download/xianyuops-1.0.1.apk")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, content)
        self.assertIn("android.package-archive", resp.headers["content-type"])

    def test_download_missing_returns_404(self) -> None:
        resp = self.client.get("/api/v1/app/download/xianyuops-9.9.9.apk")
        self.assertEqual(resp.status_code, 404)

    def test_download_traversal_returns_404(self) -> None:
        resp = self.client.get("/api/v1/app/download/..%2Fsecret.apk")
        self.assertEqual(resp.status_code, 404)


class TestReleasesDirResolution(unittest.TestCase):
    """默认 releases 目录取项目根目录（无需环境变量时也不报错）。"""

    def test_default_dir_is_project_root_releases(self) -> None:
        os.environ.pop("APP_RELEASES_DIR", None)
        path = get_releases_dir()
        self.assertEqual(path.name, "releases")
        self.assertTrue(str(path).endswith("releases"))


if __name__ == "__main__":
    unittest.main()
