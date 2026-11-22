"""
手机号注册、邮箱可选化及免限流单元测试

覆盖测试项：
1. 手机号注册成功：用户创建成功，phone 正确保存，email 为 None，password_hash 正确，role=MEMBER, status=ACTIVE。
2. 仅用户名+密码注册成功：phone 为 None，email 为 None。
3. 邮箱注册成功（无验证码）：email 正确保存且小写规范化。
4. 连续多次注册不同账号均成功：验证已移除注册限流限制。
5. 重复校验文案区分：
   - 用户名重复 → 409「用户名已被注册」
   - 手机号重复 → 409「当前手机号已经注册」
   - 邮箱重复（含大小写变体）→ 409「当前邮箱已经注册」，数据库规范化存储小写
6. 空手机号与空邮箱多次注册不冲突。
7. 注册开关关闭时拒绝注册（403「系统未开放注册，请联系管理员」），且不创建账号。
8. Schema 校验：手机号为可选字段，不填通过，填了非法格式拒绝。
9. UserService 查询：
   - get_by_phone 查询已存在用户、不存在返回 None、空参数返回 None
   - get_by_email 支持大小写不敏感查询、空参数返回 None
"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

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


# SQLite 兼容：将 BigInteger 编译为 INTEGER，以支持 SQLite 自增主键
@compiles(BigInteger, "sqlite")
def _compile_big_int_sqlite(type_, compiler, **kw):
    return "INTEGER"


class TestPhoneRegister(unittest.IsolatedAsyncioTestCase):
    """手机号与邮箱可选化注册测试集"""

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

    async def asyncTearDown(self) -> None:
        await self.session.close()
        async with self.engine.begin() as conn:
            await conn.run_sync(SystemSetting.__table__.drop)
            await conn.run_sync(User.__table__.drop)
        await self.engine.dispose()

    async def test_phone_register_success(self) -> None:
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

    async def test_register_username_password_only_success(self) -> None:
        """2. 仅用户名+密码注册成功：phone 为 None，email 为 None"""
        with patch.object(self.setting_service, "list_settings", new_callable=AsyncMock) as mock_list_settings:
            mock_list_settings.return_value = self.mock_settings

            payload = UserCreate(
                username="user_only",
                password="SecurePassword123",
            )
            response = await register_user(
                payload=payload,
                user_service=self.user_service,
                setting_service=self.setting_service,
            )

            self.assertTrue(response.success)
            self.assertEqual(response.message, "注册成功")

            user = await self.user_service.get_by_username("user_only")
            self.assertIsNotNone(user)
            self.assertEqual(user.username, "user_only")
            self.assertIsNone(user.phone)
            self.assertIsNone(user.email)

    async def test_has_email_no_code_success(self) -> None:
        """3. 邮箱注册成功（无验证码）：email 正确保存且小写规范化"""
        with patch.object(self.setting_service, "list_settings", new_callable=AsyncMock) as mock_list_settings:
            mock_list_settings.return_value = self.mock_settings

            payload = UserCreate(
                username="email_ok_user",
                email="ValidUser@Example.com",
                password="SecurePassword123",
            )
            response = await register_user(
                payload=payload,
                user_service=self.user_service,
                setting_service=self.setting_service,
            )

            self.assertTrue(response.success)
            self.assertEqual(response.message, "注册成功")

            user = await self.user_service.get_by_username("email_ok_user")
            self.assertIsNotNone(user)
            self.assertEqual(user.email, "validuser@example.com")
            self.assertIsNone(user.phone)

    async def test_same_ip_multiple_registrations_succeed(self) -> None:
        """4. 连续多次注册不同账号均成功：验证注册限流已移除"""
        with patch.object(self.setting_service, "list_settings", new_callable=AsyncMock) as mock_list_settings:
            mock_list_settings.return_value = self.mock_settings

            for i in range(10):
                payload = UserCreate(
                    username=f"fast_reg_user_{i}",
                    phone=f"13800139{i:03d}",
                    email=f"fast_user_{i}@example.com",
                    password="Password123",
                )
                response = await register_user(
                    payload=payload,
                    user_service=self.user_service,
                    setting_service=self.setting_service,
                )
                self.assertTrue(response.success)
                self.assertEqual(response.message, "注册成功")

    async def test_duplicate_username_raises_409(self) -> None:
        """5.1 用户名重复 → 409「用户名已被注册」"""
        with patch.object(self.setting_service, "list_settings", new_callable=AsyncMock) as mock_list_settings:
            mock_list_settings.return_value = self.mock_settings

            payload1 = UserCreate(username="dup_user", phone="13800138001", password="Password123")
            await register_user(payload1, self.user_service, self.setting_service)

            payload2 = UserCreate(username="dup_user", phone="13800138002", password="Password123")
            with self.assertRaises(HTTPException) as cm:
                await register_user(payload2, self.user_service, self.setting_service)

            self.assertEqual(cm.exception.status_code, 409)
            self.assertEqual(cm.exception.detail, "用户名已被注册")

    async def test_duplicate_phone_raises_409(self) -> None:
        """5.2 手机号重复 → 409「当前手机号已经注册」"""
        with patch.object(self.setting_service, "list_settings", new_callable=AsyncMock) as mock_list_settings:
            mock_list_settings.return_value = self.mock_settings

            payload1 = UserCreate(username="user_a", phone="13800138003", password="Password123")
            await register_user(payload1, self.user_service, self.setting_service)

            payload2 = UserCreate(username="user_b", phone="13800138003", password="Password123")
            with self.assertRaises(HTTPException) as cm:
                await register_user(payload2, self.user_service, self.setting_service)

            self.assertEqual(cm.exception.status_code, 409)
            self.assertEqual(cm.exception.detail, "当前手机号已经注册")

    async def test_duplicate_email_raises_409_and_case_insensitive(self) -> None:
        """5.3 邮箱重复（含大小写变体）→ 409「当前邮箱已经注册」，数据库规范化存储小写"""
        with patch.object(self.setting_service, "list_settings", new_callable=AsyncMock) as mock_list_settings:
            mock_list_settings.return_value = self.mock_settings

            payload1 = UserCreate(
                username="mail_user_1",
                email="Test@Example.com",
                password="Password123",
            )
            response1 = await register_user(payload1, self.user_service, self.setting_service)
            self.assertTrue(response1.success)

            # 数据库中存储为小写
            user1 = await self.user_service.get_by_username("mail_user_1")
            self.assertIsNotNone(user1)
            self.assertEqual(user1.email, "test@example.com")

            # 第二次使用全小写注册相同邮箱 → 409「当前邮箱已经注册」
            payload2 = UserCreate(
                username="mail_user_2",
                email="test@example.com",
                password="Password123",
            )
            with self.assertRaises(HTTPException) as cm:
                await register_user(payload2, self.user_service, self.setting_service)

            self.assertEqual(cm.exception.status_code, 409)
            self.assertEqual(cm.exception.detail, "当前邮箱已经注册")

    async def test_empty_phone_and_email_multiple_registrations_no_conflict(self) -> None:
        """6. 空手机号与空邮箱多次注册不冲突"""
        with patch.object(self.setting_service, "list_settings", new_callable=AsyncMock) as mock_list_settings:
            mock_list_settings.return_value = self.mock_settings

            payload1 = UserCreate(username="empty_field_user1", password="Password123")
            response1 = await register_user(payload1, self.user_service, self.setting_service)
            self.assertTrue(response1.success)

            payload2 = UserCreate(username="empty_field_user2", password="Password123")
            response2 = await register_user(payload2, self.user_service, self.setting_service)
            self.assertTrue(response2.success)

    async def test_registration_disabled_returns_403(self) -> None:
        """7. 开关关闭时注册被拒绝（返回 403 与「系统未开放注册，请联系管理员」），且不创建账号"""
        with patch.object(self.setting_service, "list_settings", new_callable=AsyncMock) as mock_list_settings:
            mock_list_settings.return_value = {"registration_enabled": "false"}

            payload = UserCreate(
                username="closed_user",
                phone="13800138020",
                password="Password123",
            )
            response = await register_user(
                payload=payload,
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
        """8. Schema 校验：手机号为可选字段，不填通过，填了非法格式拒绝"""
        # 缺少 phone 可选字段，合法通过
        no_phone = UserCreate(username="no_phone", password="Password123")
        self.assertIsNone(no_phone.phone)
        self.assertIsNone(no_phone.email)

        # 合法手机号通过
        valid = UserCreate(username="valid_p", phone="13800138000", password="Password123")
        self.assertEqual(valid.phone, "13800138000")
        self.assertIsNone(valid.email)

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
        """9. UserService 查询：get_by_phone 与 get_by_email 大小写不敏感及空值测试"""
        payload = UserCreate(
            username="query_user",
            phone="13800138099",
            email="QueryUser@Example.com",
            password="Password123",
        )
        created = await self.user_service.create(payload)

        # get_by_phone 查询存在用户
        found_phone = await self.user_service.get_by_phone("13800138099")
        self.assertIsNotNone(found_phone)
        self.assertEqual(found_phone.id, created.id)

        # get_by_phone 查询不存在的号码
        self.assertIsNone(await self.user_service.get_by_phone("13800138000"))

        # get_by_phone(None) 与空字符串
        self.assertIsNone(await self.user_service.get_by_phone(None))
        self.assertIsNone(await self.user_service.get_by_phone(""))

        # get_by_email 查询大小写不敏感
        found_email = await self.user_service.get_by_email("queryuser@example.com")
        self.assertIsNotNone(found_email)
        self.assertEqual(found_email.id, created.id)

        found_email_upper = await self.user_service.get_by_email("QUERYUSER@EXAMPLE.COM")
        self.assertIsNotNone(found_email_upper)
        self.assertEqual(found_email_upper.id, created.id)

        # get_by_email(None) 与空字符串返回 None
        self.assertIsNone(await self.user_service.get_by_email(None))
        self.assertIsNone(await self.user_service.get_by_email(""))


if __name__ == "__main__":
    unittest.main()
