"""
用户登录支持用户名、手机号、邮箱三种标识及兼容旧字段单元测试

覆盖测试项：
1. 三种标识登录成功：
   - identifier 为用户名 + 密码登录成功
   - identifier 为手机号 + 密码登录成功
   - identifier 为邮箱 + 密码登录成功
   - identifier 为邮箱时大小写不敏感变体登录成功
2. 兼容旧字段登录：
   - username + password 登录成功
   - email + password 登录成功
3. 错误密码与不存在用户提示文案：
   - 手机号方式密码错误 → 提示含「手机号或密码错误」
   - 手机号方式用户不存在 → 提示含「手机号或密码错误」
   - 用户名方式密码错误 → 提示含「用户名或密码错误」
   - 用户名方式用户不存在 → 提示含「用户名或密码错误」
   - 邮箱方式密码错误 → 提示含「邮箱或密码错误」
   - 邮箱方式用户不存在 → 提示含「邮箱或密码错误」
4. 禁用账号登录被拒。
5. 无效登录请求兜底拦截。
"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import BigInteger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.core import security
from app.services.auth import AuthService
from app.services.login_flow_service import perform_login
from app.services.user_login_log_service import (
    LOGIN_METHOD_EMAIL_PASSWORD,
    LOGIN_METHOD_PHONE_PASSWORD,
    LOGIN_METHOD_USERNAME_PASSWORD,
    UserLoginLogService,
)
from common.models.system_setting import SystemSetting
from common.models.user import User, UserRole, UserStatus
from common.schemas.auth import LoginRequest
from common.services.auth_rate_limit_service import LoginAttempt


# SQLite 兼容：将 BigInteger 编译为 INTEGER，以支持 SQLite 自增主键
@compiles(BigInteger, "sqlite")
def _compile_big_int_sqlite(type_, compiler, **kw):
    return "INTEGER"


class TestLoginIdentifier(unittest.IsolatedAsyncioTestCase):
    """登录支持三种标识与旧字段兼容测试集"""

    async def asyncSetUp(self) -> None:
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
        self.auth_service = AuthService(self.session)
        self.login_log_service = UserLoginLogService(self.session)

        # 禁用滑块验证码，避免影响单测
        captcha_setting = SystemSetting(key="login_captcha_enabled", value="false")
        self.session.add(captcha_setting)

        # 创建测试用户
        hashed_pwd = security.get_password_hash("Password123")
        self.active_user = User(
            username="testuser",
            phone="13800000001",
            email="test@example.com",
            password_hash=hashed_pwd,
            role=UserRole.MEMBER,
            status=UserStatus.ACTIVE,
        )
        self.disabled_user = User(
            username="disabled_user",
            phone="13800000002",
            email="disabled@example.com",
            password_hash=hashed_pwd,
            role=UserRole.MEMBER,
            status=UserStatus.INACTIVE,
        )
        self.session.add_all([self.active_user, self.disabled_user])
        await self.session.commit()

        # Mock Redis 限流占位与结束操作
        self.mock_attempt = LoginAttempt(keys=(), request_id="mock_request_id")
        self.patcher_begin = patch("app.services.login_flow_service.begin_login_attempt", new_callable=AsyncMock)
        self.patcher_finish = patch("app.services.login_flow_service.finish_login_attempt", new_callable=AsyncMock)
        self.mock_begin = self.patcher_begin.start()
        self.mock_finish = self.patcher_finish.start()
        self.mock_begin.return_value = self.mock_attempt
        self.mock_finish.return_value = None

        # Mock 登录审计日志记录
        self.patcher_log = patch.object(self.login_log_service, "record", new_callable=AsyncMock)
        self.mock_log = self.patcher_log.start()
        self.mock_log.return_value = None

    async def asyncTearDown(self) -> None:
        self.patcher_log.stop()
        self.patcher_finish.stop()
        self.patcher_begin.stop()

        await self.session.close()
        async with self.engine.begin() as conn:
            await conn.run_sync(SystemSetting.__table__.drop)
            await conn.run_sync(User.__table__.drop)
        await self.engine.dispose()

    async def test_login_by_username_identifier(self) -> None:
        """1.1 identifier 为用户名 + 密码登录成功"""
        payload = LoginRequest(identifier="testuser", password="Password123")
        response = await perform_login(
            payload, "127.0.0.1", self.auth_service, self.session, self.login_log_service
        )
        self.assertTrue(response.success)
        self.assertEqual(response.username, "testuser")
        self.assertIsNotNone(response.token)

        # 验证审计日志记录的方法为用户名密码
        self.mock_log.assert_awaited()
        call_kwargs = self.mock_log.call_args[1]
        self.assertEqual(call_kwargs["login_method"], LOGIN_METHOD_USERNAME_PASSWORD)
        self.assertEqual(call_kwargs["login_account"], "testuser")
        self.assertTrue(call_kwargs["success"])

    async def test_login_by_phone_identifier(self) -> None:
        """1.2 identifier 为手机号 + 密码登录成功"""
        payload = LoginRequest(identifier="13800000001", password="Password123")
        response = await perform_login(
            payload, "127.0.0.1", self.auth_service, self.session, self.login_log_service
        )
        self.assertTrue(response.success)
        self.assertEqual(response.username, "testuser")
        self.assertIsNotNone(response.token)

        # 验证审计日志记录的方法为手机号密码
        call_kwargs = self.mock_log.call_args[1]
        self.assertEqual(call_kwargs["login_method"], LOGIN_METHOD_PHONE_PASSWORD)
        self.assertEqual(call_kwargs["login_account"], "13800000001")
        self.assertTrue(call_kwargs["success"])

    async def test_login_by_email_identifier(self) -> None:
        """1.3 identifier 为邮箱 + 密码登录成功"""
        payload = LoginRequest(identifier="test@example.com", password="Password123")
        response = await perform_login(
            payload, "127.0.0.1", self.auth_service, self.session, self.login_log_service
        )
        self.assertTrue(response.success)
        self.assertEqual(response.username, "testuser")
        self.assertIsNotNone(response.token)

        call_kwargs = self.mock_log.call_args[1]
        self.assertEqual(call_kwargs["login_method"], LOGIN_METHOD_EMAIL_PASSWORD)
        self.assertEqual(call_kwargs["login_account"], "test@example.com")
        self.assertTrue(call_kwargs["success"])

    async def test_login_by_email_identifier_case_insensitive(self) -> None:
        """1.4 identifier 为邮箱时大小写不敏感变体登录成功"""
        payload = LoginRequest(identifier="Test@EXAMPLE.com", password="Password123")
        response = await perform_login(
            payload, "127.0.0.1", self.auth_service, self.session, self.login_log_service
        )
        self.assertTrue(response.success)
        self.assertEqual(response.username, "testuser")

    async def test_login_legacy_username_field(self) -> None:
        """2.1 兼容旧字段：username + password 登录成功"""
        payload = LoginRequest(username="testuser", password="Password123")
        response = await perform_login(
            payload, "127.0.0.1", self.auth_service, self.session, self.login_log_service
        )
        self.assertTrue(response.success)
        self.assertEqual(response.username, "testuser")

    async def test_login_legacy_email_field(self) -> None:
        """2.2 兼容旧字段：email + password 登录成功"""
        payload = LoginRequest(email="test@example.com", password="Password123")
        response = await perform_login(
            payload, "127.0.0.1", self.auth_service, self.session, self.login_log_service
        )
        self.assertTrue(response.success)
        self.assertEqual(response.username, "testuser")

    async def test_login_phone_wrong_password(self) -> None:
        """3.1 手机号方式密码错误 → 提示含「手机号或密码错误」"""
        payload = LoginRequest(identifier="13800000001", password="WrongPassword")
        response = await perform_login(
            payload, "127.0.0.1", self.auth_service, self.session, self.login_log_service
        )
        self.assertFalse(response.success)
        self.assertIn("手机号或密码错误", response.message)

    async def test_login_phone_user_not_found(self) -> None:
        """3.2 手机号方式用户不存在 → 提示含「手机号或密码错误」"""
        payload = LoginRequest(identifier="13999999999", password="Password123")
        response = await perform_login(
            payload, "127.0.0.1", self.auth_service, self.session, self.login_log_service
        )
        self.assertFalse(response.success)
        self.assertIn("手机号或密码错误", response.message)

    async def test_login_username_wrong_password(self) -> None:
        """3.3 用户名方式密码错误 → 提示含「用户名或密码错误」"""
        payload = LoginRequest(identifier="testuser", password="WrongPassword")
        response = await perform_login(
            payload, "127.0.0.1", self.auth_service, self.session, self.login_log_service
        )
        self.assertFalse(response.success)
        self.assertIn("用户名或密码错误", response.message)

    async def test_login_username_not_found(self) -> None:
        """3.4 用户名方式用户不存在 → 提示含「用户名或密码错误」"""
        payload = LoginRequest(identifier="nosuchuser", password="Password123")
        response = await perform_login(
            payload, "127.0.0.1", self.auth_service, self.session, self.login_log_service
        )
        self.assertFalse(response.success)
        self.assertIn("用户名或密码错误", response.message)

    async def test_login_email_wrong_password(self) -> None:
        """3.5 邮箱方式密码错误 → 提示含「邮箱或密码错误」"""
        payload = LoginRequest(identifier="test@example.com", password="WrongPassword")
        response = await perform_login(
            payload, "127.0.0.1", self.auth_service, self.session, self.login_log_service
        )
        self.assertFalse(response.success)
        self.assertIn("邮箱或密码错误", response.message)

    async def test_login_email_not_found(self) -> None:
        """3.6 邮箱方式用户不存在 → 提示含「邮箱或密码错误」"""
        payload = LoginRequest(identifier="unknown@example.com", password="Password123")
        response = await perform_login(
            payload, "127.0.0.1", self.auth_service, self.session, self.login_log_service
        )
        self.assertFalse(response.success)
        self.assertIn("邮箱或密码错误", response.message)

    async def test_login_disabled_user(self) -> None:
        """4. 禁用账号登录被拒"""
        payload = LoginRequest(identifier="disabled_user", password="Password123")
        response = await perform_login(
            payload, "127.0.0.1", self.auth_service, self.session, self.login_log_service
        )
        self.assertFalse(response.success)
        self.assertEqual(response.message, "账号已禁用，请联系管理员")

    async def test_login_invalid_payload(self) -> None:
        """5. 无效登录请求兜底拦截（缺少密码）"""
        payload = LoginRequest(identifier="testuser")
        response = await perform_login(
            payload, "127.0.0.1", self.auth_service, self.session, self.login_log_service
        )
        self.assertFalse(response.success)
        self.assertEqual(response.message, "请提供有效的登录信息")


if __name__ == "__main__":
    unittest.main()
