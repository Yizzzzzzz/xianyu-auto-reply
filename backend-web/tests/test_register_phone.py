"""
手机号注册与邮箱可选化单元测试

覆盖测试项：
1. 手机号注册成功：用户创建成功，phone 正确保存，email 为 None，password_hash 不是明文且通过验证，role 为 MEMBER、status 为 ACTIVE。
2. 用户名重复 → 409；手机号重复 → 409；邮箱重复 → 409。
3. 无 email 时不要求验证码（直接注册成功）。
4. 有 email 无验证码 → 400；有 email 且验证码正确时注册成功；验证码错误 → 400。
5. 开关关闭时注册被拒绝（返回 403 与拒绝文案「系统未开放注册，请联系管理员」），且不创建账号。
6. Schema 校验：手机号格式非法（123、10位、12位等）或缺少 phone 时拒绝。
7. UserService.get_by_phone 查询已存在用户；get_by_email(None) 返回 None；get_by_phone(None) 返回 None。
8. 限流适配：check_registration_limit 传入 email=None 时按手机号限流且不抛 AttributeError。
"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import BigInteger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.api.routes.auth import register_user
from app.core.security import verify_password
from app.services.system_setting_service import SystemSettingService
from app.services.user_service import UserService
from common.models.system_setting import SystemSetting
from common.models.user import User, UserRole, UserStatus
from common.schemas.user import UserCreate
from common.services.auth_rate_limit_service import (
    AuthRateLimitRejected,
    check_registration_limit,
)

# SQLite 兼容：将 BigInteger 编译为 INTEGER，以支持 SQLite 自增主键
@compiles(BigInteger, "sqlite")
def _compile_big_int_sqlite(type_, compiler, **kw):
    return "INTEGER"


class TestPhoneRegister(unittest.IsolatedAsyncioTestCase):
    """手机号注册与账号创建测试集"""

    async def asyncSetUp(self) -> None:
        # 初始化 SQLite 内存数据库
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(User.__table__.create)
            await conn.run_sync(SystemSetting.__table__.create)

        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.session: AsyncSession = self.session_factory()
        self.user_service = UserService(self.session)
        self.setting_service = SystemSettingService(self.session)

        # 默认开启注册
        self.mock_settings = {"registration_enabled": "true"}

        # 创建请求对象 Mock
        self.mock_request = MagicMock()
        self.mock_request.client.host = "127.0.0.1"
        self.mock_request.headers = {}

    async def asyncTearDown(self) -> None:
        await self.session.close()
        async with self.engine.begin() as conn:
            await conn.run_sync(SystemSetting.__table__.drop)
            await conn.run_sync(User.__table__.drop)
        await self.engine.dispose()

    @patch("app.api.routes.auth.check_registration_limit", new_callable=AsyncMock)
    @patch("app.api.routes.auth.check_email_code")
    async def test_phone_register_success(self, mock_check_code, mock_check_limit) -> None:
        """1. 手机号注册成功：用户创建成功，phone 正确保存，email 为 None，password_hash 正确，role=MEMBER, status=ACTIVE"""
        with patch.object(self.setting_service, "list_settings", new_callable=AsyncMock) as mock_list_settings:
            mock_list_settings.return_value = self.mock_settings

            payload = UserCreate(
                username="phone_user_1",
                phone="13800138000",
                password="SecurePassword123",
            )
            response = await register_user(
                payload=payload,
                request=self.mock_request,
                user_service=self.user_service,
                setting_service=self.setting_service,
            )

            self.assertTrue(response.success)
            self.assertEqual(response.message, "注册成功")

            # 校验数据库中用户数据
            user = await self.user_service.get_by_username("phone_user_1")
            self.assertIsNotNone(user)
            self.assertEqual(user.username, "phone_user_1")
            self.assertEqual(user.phone, "13800138000")
            self.assertIsNone(user.email)
            self.assertNotEqual(user.password_hash, "SecurePassword123")
            self.assertTrue(verify_password("SecurePassword123", user.password_hash))
            self.assertEqual(user.role, UserRole.MEMBER)
            self.assertEqual(user.status, UserStatus.ACTIVE)

            # 验证限流调用传参
            mock_check_limit.assert_awaited_once_with("127.0.0.1", None, phone="13800138000")
            # 邮箱验证码未被调用
            mock_check_code.assert_not_called()

    @patch("app.api.routes.auth.check_registration_limit", new_callable=AsyncMock)
    async def test_duplicate_username_raises_409(self, mock_check_limit) -> None:
        """2.1 用户名重复 → 409"""
        with patch.object(self.setting_service, "list_settings", new_callable=AsyncMock) as mock_list_settings:
            mock_list_settings.return_value = self.mock_settings

            # 第一次注册成功
            payload1 = UserCreate(username="dup_user", phone="13800138001", password="Password123")
            await register_user(payload1, self.mock_request, self.user_service, self.setting_service)

            # 第二次相同用户名注册
            payload2 = UserCreate(username="dup_user", phone="13800138002", password="Password123")
            with self.assertRaises(HTTPException) as cm:
                await register_user(payload2, self.mock_request, self.user_service, self.setting_service)

            self.assertEqual(cm.exception.status_code, 409)
            self.assertEqual(cm.exception.detail, "用户名已被注册")

    @patch("app.api.routes.auth.check_registration_limit", new_callable=AsyncMock)
    async def test_duplicate_phone_raises_409(self, mock_check_limit) -> None:
        """2.2 手机号重复 → 409"""
        with patch.object(self.setting_service, "list_settings", new_callable=AsyncMock) as mock_list_settings:
            mock_list_settings.return_value = self.mock_settings

            # 第一次注册成功
            payload1 = UserCreate(username="user_a", phone="13800138003", password="Password123")
            await register_user(payload1, self.mock_request, self.user_service, self.setting_service)

            # 第二次不同用户名，相同手机号注册
            payload2 = UserCreate(username="user_b", phone="13800138003", password="Password123")
            with self.assertRaises(HTTPException) as cm:
                await register_user(payload2, self.mock_request, self.user_service, self.setting_service)

            self.assertEqual(cm.exception.status_code, 409)
            self.assertEqual(cm.exception.detail, "手机号已被注册")

    @patch("app.api.routes.auth.check_registration_limit", new_callable=AsyncMock)
    @patch("app.api.routes.auth.check_email_code")
    async def test_no_email_no_verification_code_needed(self, mock_check_code, mock_check_limit) -> None:
        """3. 无 email 时不要求验证码（直接注册成功）"""
        with patch.object(self.setting_service, "list_settings", new_callable=AsyncMock) as mock_list_settings:
            mock_list_settings.return_value = self.mock_settings

            payload = UserCreate(
                username="no_email_user",
                phone="13912345678",
                email=None,
                verification_code=None,
                password="Password123",
            )
            response = await register_user(payload, self.mock_request, self.user_service, self.setting_service)
            self.assertTrue(response.success)
            mock_check_code.assert_not_called()

    @patch("app.api.routes.auth.check_registration_limit", new_callable=AsyncMock)
    @patch("app.api.routes.auth.enforce_email_domain", new_callable=AsyncMock)
    async def test_has_email_without_code_raises_400(self, mock_enforce_domain, mock_check_limit) -> None:
        """4.1 有 email 无验证码 → 400「请输入邮箱验证码」"""
        with patch.object(self.setting_service, "list_settings", new_callable=AsyncMock) as mock_list_settings:
            mock_list_settings.return_value = self.mock_settings

            payload = UserCreate(
                username="email_no_code",
                phone="13912345679",
                email="test@example.com",
                password="Password123",
                verification_code=None,
            )
            with self.assertRaises(HTTPException) as cm:
                await register_user(payload, self.mock_request, self.user_service, self.setting_service)

            self.assertEqual(cm.exception.status_code, 400)
            self.assertEqual(cm.exception.detail, "请输入邮箱验证码")
            mock_enforce_domain.assert_awaited_once_with(self.setting_service.session, "test@example.com")

    @patch("app.api.routes.auth.check_registration_limit", new_callable=AsyncMock)
    @patch("app.api.routes.auth.enforce_email_domain", new_callable=AsyncMock)
    @patch("app.api.routes.auth.check_email_code")
    async def test_has_email_with_valid_code_success(
        self, mock_check_code, mock_enforce_domain, mock_check_limit
    ) -> None:
        """4.2 有 email 且验证码正确时走原邮箱分支成功"""
        mock_check_code.return_value = (True, "验证通过")
        with patch.object(self.setting_service, "list_settings", new_callable=AsyncMock) as mock_list_settings:
            mock_list_settings.return_value = self.mock_settings

            payload = UserCreate(
                username="email_ok_user",
                phone="13912345680",
                email="valid@example.com",
                password="Password123",
                verification_code="123456",
            )
            response = await register_user(payload, self.mock_request, self.user_service, self.setting_service)
            self.assertTrue(response.success)

            user = await self.user_service.get_by_username("email_ok_user")
            self.assertIsNotNone(user)
            self.assertEqual(user.email, "valid@example.com")
            self.assertEqual(user.phone, "13912345680")
            mock_check_code.assert_called_once_with("valid@example.com", "123456", "register")

    @patch("app.api.routes.auth.check_registration_limit", new_callable=AsyncMock)
    @patch("app.api.routes.auth.enforce_email_domain", new_callable=AsyncMock)
    @patch("app.api.routes.auth.check_email_code")
    async def test_has_email_with_invalid_code_raises_400(
        self, mock_check_code, mock_enforce_domain, mock_check_limit
    ) -> None:
        """4.3 有 email 验证码错误 → 400"""
        mock_check_code.return_value = (False, "验证码错误或已失效")
        with patch.object(self.setting_service, "list_settings", new_callable=AsyncMock) as mock_list_settings:
            mock_list_settings.return_value = self.mock_settings

            payload = UserCreate(
                username="email_bad_code",
                phone="13912345681",
                email="bad@example.com",
                password="Password123",
                verification_code="000000",
            )
            with self.assertRaises(HTTPException) as cm:
                await register_user(payload, self.mock_request, self.user_service, self.setting_service)

            self.assertEqual(cm.exception.status_code, 400)
            self.assertEqual(cm.exception.detail, "验证码错误或已失效")

    @patch("app.api.routes.auth.check_registration_limit", new_callable=AsyncMock)
    @patch("app.api.routes.auth.enforce_email_domain", new_callable=AsyncMock)
    @patch("app.api.routes.auth.check_email_code")
    async def test_has_email_duplicate_raises_409(
        self, mock_check_code, mock_enforce_domain, mock_check_limit
    ) -> None:
        """4.4 提供了 email 且冲突 → 409「邮箱已被注册」"""
        mock_check_code.return_value = (True, "验证通过")
        with patch.object(self.setting_service, "list_settings", new_callable=AsyncMock) as mock_list_settings:
            mock_list_settings.return_value = self.mock_settings

            # 第一次注册成功
            payload1 = UserCreate(
                username="mail_user_1",
                phone="13800138010",
                email="dup@example.com",
                password="Password123",
                verification_code="123456",
            )
            await register_user(payload1, self.mock_request, self.user_service, self.setting_service)

            # 第二次使用相同 email 注册
            payload2 = UserCreate(
                username="mail_user_2",
                phone="13800138011",
                email="dup@example.com",
                password="Password123",
                verification_code="123456",
            )
            with self.assertRaises(HTTPException) as cm:
                await register_user(payload2, self.mock_request, self.user_service, self.setting_service)

            self.assertEqual(cm.exception.status_code, 409)
            self.assertEqual(cm.exception.detail, "邮箱已被注册")

    @patch("app.api.routes.auth.check_registration_limit", new_callable=AsyncMock)
    async def test_registration_disabled_returns_403(self, mock_check_limit) -> None:
        """5. 开关关闭时注册被拒绝（返回 403 与「系统未开放注册，请联系管理员」），且不创建账号"""
        with patch.object(self.setting_service, "list_settings", new_callable=AsyncMock) as mock_list_settings:
            mock_list_settings.return_value = {"registration_enabled": "false"}

            payload = UserCreate(
                username="closed_user",
                phone="13800138020",
                password="Password123",
            )
            response = await register_user(
                payload=payload,
                request=self.mock_request,
                user_service=self.user_service,
                setting_service=self.setting_service,
            )

            self.assertFalse(response.success)
            self.assertEqual(response.code, 403)
            self.assertEqual(response.message, "系统未开放注册，请联系管理员")

            # 确认数据库中未创建账号
            user = await self.user_service.get_by_username("closed_user")
            self.assertIsNone(user)

    def test_schema_phone_validation(self) -> None:
        """6. Schema 校验：非法手机号或缺 phone 时拒绝"""
        # 合法手机号通过
        valid = UserCreate(username="valid_p", phone="13800138000", password="Password123")
        self.assertEqual(valid.phone, "13800138000")
        self.assertIsNone(valid.email)

        # 缺少 phone 必填字段
        with self.assertRaises(ValidationError):
            UserCreate(username="no_phone", password="Password123")  # type: ignore

        # 手机号格式非法：位数过短（3位）
        with self.assertRaises(ValidationError):
            UserCreate(username="bad_p1", phone="123", password="Password123")

        # 手机号格式非法：10位
        with self.assertRaises(ValidationError):
            UserCreate(username="bad_p2", phone="1381234567", password="Password123")

        # 手机号格式非法：12位
        with self.assertRaises(ValidationError):
            UserCreate(username="bad_p3", phone="138123456789", password="Password123")

        # 手机号格式非法：开头不是 1[3-9]
        with self.assertRaises(ValidationError):
            UserCreate(username="bad_p4", phone="12812345678", password="Password123")
        with self.assertRaises(ValidationError):
            UserCreate(username="bad_p5", phone="23812345678", password="Password123")

    async def test_user_service_queries(self) -> None:
        """7. get_by_phone 查询；get_by_email(None) 返回 None；get_by_phone(None) 返回 None"""
        payload = UserCreate(username="query_user", phone="13800138099", password="Password123")
        created = await self.user_service.create(payload)

        # get_by_phone 查询存在用户
        found = await self.user_service.get_by_phone("13800138099")
        self.assertIsNotNone(found)
        self.assertEqual(found.id, created.id)

        # get_by_phone 查询不存在的号码
        not_found = await self.user_service.get_by_phone("13800138000")
        self.assertIsNone(not_found)

        # get_by_phone(None) 与空字符串
        self.assertIsNone(await self.user_service.get_by_phone(None))
        self.assertIsNone(await self.user_service.get_by_phone(""))

        # get_by_email(None) 与空字符串返回 None（不会误判）
        self.assertIsNone(await self.user_service.get_by_email(None))
        self.assertIsNone(await self.user_service.get_by_email(""))

    @patch("common.services.auth_rate_limit_service.check_sliding_window_limit")
    async def test_rate_limit_phone_and_none_email(self, mock_sliding_window) -> None:
        """8. check_registration_limit 传入 email=None 时按手机号限流且不报错"""
        # 模拟限流通过
        mock_sliding_window.return_value = MagicMock(allowed=True)
        await check_registration_limit("127.0.0.1", None, phone="13800138000")

        # 验证调用了 IP 和手机号滑窗
        self.assertEqual(mock_sliding_window.call_count, 2)
        ip_call = mock_sliding_window.call_args_list[0]
        self.assertEqual(ip_call[0][0], "auth:register:ip")
        phone_call = mock_sliding_window.call_args_list[1]
        self.assertEqual(phone_call[0][0], "auth:register:phone")
        self.assertEqual(phone_call[0][1], "13800138000")

        # 模拟手机号超限
        mock_sliding_window.reset_mock()
        mock_sliding_window.side_effect = [
            MagicMock(allowed=True),  # IP allowed
            MagicMock(allowed=False),  # phone blocked
        ]
        with self.assertRaises(AuthRateLimitRejected) as cm:
            await check_registration_limit("127.0.0.1", None, phone="13800138000")
        self.assertIn("该手机号24小时内已尝试注册", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
