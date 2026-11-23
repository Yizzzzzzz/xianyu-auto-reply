"""
用户个人信息查询、修改及改密单元测试

覆盖测试项：
1. GET /users/me: 返回当前登录用户的 UserPublic 详情。
2. PUT /users/me 手机号更新成功：phone 正确保存并返回。
3. PUT /users/me 手机号格式非法：非 ^1[3-9]\\d{9}$ 返回 400 手机号格式不正确。
4. PUT /users/me 手机号已被他人绑定：返回 409 当前手机号已经注册。
5. PUT /users/me 手机号与本人一致：更新成功不报冲突。
6. PUT /users/me 清空手机号：显式传 null 或空串清空手机号为 None。
7. PUT /users/me 邮箱更新成功：email 规范化小写保存并返回。
8. PUT /users/me 邮箱已被他人绑定：返回 409 当前邮箱已经注册。
9. PUT /users/me 清空邮箱：显式传 null 或空串清空邮箱为 None。
10. POST /users/change-password:
    - 当前密码错误 → 400 当前密码不正确
    - 新密码少于6位 → 400 新密码长度不能少于6位
    - 正常修改密码 → 200 密码修改成功，密码哈希更新
"""
from __future__ import annotations

import unittest
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import BigInteger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.api.routes.users import (
    ChangePasswordRequest,
    change_password,
    read_current_user,
    update_current_user,
)
from app.core.security import get_password_hash, verify_password
from app.services.user_service import UserService
from common.models.user import User, UserRole, UserStatus
from common.schemas.user import UserUpdate


@compiles(BigInteger, "sqlite")
def _compile_big_int_sqlite(type_, compiler, **kw):
    return "INTEGER"


class TestProfileUpdate(unittest.IsolatedAsyncioTestCase):
    """用户个人信息与改密测试集"""

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(User.__table__.create)

        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.session: AsyncSession = self.session_factory()
        self.user_service = UserService(self.session)

        # 预设用户1 (当前用户) 与 用户2 (用于唯一性冲突测试)
        self.user1 = User(
            id=1,
            username="user_test1",
            email="user1@example.com",
            phone="13800138001",
            password_hash=get_password_hash("OldPassword123"),
            role=UserRole.MEMBER,
            status=UserStatus.ACTIVE,
            account_limit=5,
            expire_at=datetime(2027, 1, 1, 0, 0, 0),
        )
        self.user2 = User(
            id=2,
            username="user_test2",
            email="user2@example.com",
            phone="13800138002",
            password_hash=get_password_hash("Password123"),
            role=UserRole.MEMBER,
            status=UserStatus.ACTIVE,
        )
        self.session.add_all([self.user1, self.user2])
        await self.session.commit()

    async def asyncTearDown(self) -> None:
        await self.session.close()
        async with self.engine.begin() as conn:
            await conn.run_sync(User.__table__.drop)
        await self.engine.dispose()

    async def test_get_me(self) -> None:
        """1. GET /users/me: 返回当前用户公共信息"""
        res = await read_current_user(current_user=self.user1)
        self.assertEqual(res.id, 1)
        self.assertEqual(res.username, "user_test1")
        self.assertEqual(res.email, "user1@example.com")
        self.assertEqual(res.phone, "13800138001")
        self.assertEqual(res.role, UserRole.MEMBER)
        self.assertEqual(res.status, UserStatus.ACTIVE)
        self.assertEqual(res.account_limit, 5)

    async def test_update_phone_success(self) -> None:
        """2. PUT /users/me: 更新手机号成功"""
        payload = UserUpdate(phone="13912345678")
        res = await update_current_user(
            payload=payload,
            current_user=self.user1,
            user_service=self.user_service,
            session=self.session,
        )
        self.assertEqual(res.phone, "13912345678")
        db_user = await self.user_service.get(1)
        self.assertEqual(db_user.phone, "13912345678")

    async def test_update_phone_invalid_format(self) -> None:
        """3. PUT /users/me: 手机号格式非法抛 400"""
        for invalid_phone in ["12345", "12800138000", "138001380000", "abcdefghijk"]:
            with self.subTest(invalid_phone=invalid_phone):
                payload = UserUpdate(phone=invalid_phone)
                with self.assertRaises(HTTPException) as cm:
                    await update_current_user(
                        payload=payload,
                        current_user=self.user1,
                        user_service=self.user_service,
                        session=self.session,
                    )
                self.assertEqual(cm.exception.status_code, 400)
                self.assertEqual(cm.exception.detail, "手机号格式不正确")

    async def test_update_phone_duplicate(self) -> None:
        """4. PUT /users/me: 手机号已被其他用户占用抛 409"""
        payload = UserUpdate(phone="13800138002")  # user2 的手机号
        with self.assertRaises(HTTPException) as cm:
            await update_current_user(
                payload=payload,
                current_user=self.user1,
                user_service=self.user_service,
                session=self.session,
            )
        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(cm.exception.detail, "当前手机号已经注册")

    async def test_update_phone_same_as_own(self) -> None:
        """5. PUT /users/me: 手机号与自己当前一致不报错"""
        payload = UserUpdate(phone="13800138001")
        res = await update_current_user(
            payload=payload,
            current_user=self.user1,
            user_service=self.user_service,
            session=self.session,
        )
        self.assertEqual(res.phone, "13800138001")

    async def test_update_phone_clear(self) -> None:
        """6. PUT /users/me: 清空手机号"""
        payload = UserUpdate(phone=None)
        res = await update_current_user(
            payload=payload,
            current_user=self.user1,
            user_service=self.user_service,
            session=self.session,
        )
        self.assertIsNone(res.phone)
        db_user = await self.user_service.get(1)
        self.assertIsNone(db_user.phone)

    async def test_update_email_success(self) -> None:
        """7. PUT /users/me: 更新邮箱成功并小写规范化"""
        payload = UserUpdate(email="NEW_EMAIL@EXAMPLE.COM")
        res = await update_current_user(
            payload=payload,
            current_user=self.user1,
            user_service=self.user_service,
            session=self.session,
        )
        self.assertEqual(res.email, "new_email@example.com")
        db_user = await self.user_service.get(1)
        self.assertEqual(db_user.email, "new_email@example.com")

    async def test_update_email_duplicate(self) -> None:
        """8. PUT /users/me: 邮箱已被他人占用抛 409"""
        payload = UserUpdate(email="USER2@example.com")
        with self.assertRaises(HTTPException) as cm:
            await update_current_user(
                payload=payload,
                current_user=self.user1,
                user_service=self.user_service,
                session=self.session,
            )
        self.assertEqual(cm.exception.status_code, 409)
        self.assertEqual(cm.exception.detail, "当前邮箱已经注册")

    async def test_update_email_clear(self) -> None:
        """9. PUT /users/me: 清空邮箱（传 None 或空串）"""
        payload = UserUpdate.model_validate({"email": None})
        res = await update_current_user(
            payload=payload,
            current_user=self.user1,
            user_service=self.user_service,
            session=self.session,
        )
        self.assertIsNone(res.email)
        db_user = await self.user_service.get(1)
        self.assertIsNone(db_user.email)

    async def test_change_password_success(self) -> None:
        """10. POST /users/change-password: 正常修改密码"""
        payload = ChangePasswordRequest(
            current_password="OldPassword123",
            new_password="BrandNewPassword456",
        )
        res = await change_password(
            payload=payload,
            current_user=self.user1,
            user_service=self.user_service,
        )
        self.assertTrue(res.success)
        self.assertEqual(res.message, "密码修改成功")
        # 验证新密码哈希
        db_user = await self.user_service.get(1)
        self.assertTrue(verify_password("BrandNewPassword456", db_user.password_hash))
        self.assertFalse(verify_password("OldPassword123", db_user.password_hash))

    async def test_change_password_wrong_current(self) -> None:
        """11. POST /users/change-password: 当前密码错误抛 400"""
        payload = ChangePasswordRequest(
            current_password="WrongPassword999",
            new_password="BrandNewPassword456",
        )
        with self.assertRaises(HTTPException) as cm:
            await change_password(
                payload=payload,
                current_user=self.user1,
                user_service=self.user_service,
            )
        self.assertEqual(cm.exception.status_code, 400)
        self.assertEqual(cm.exception.detail, "当前密码不正确")

    async def test_change_password_too_short(self) -> None:
        """12. POST /users/change-password: 新密码小于6位抛 400"""
        payload = ChangePasswordRequest(
            current_password="OldPassword123",
            new_password="12345",
        )
        with self.assertRaises(HTTPException) as cm:
            await change_password(
                payload=payload,
                current_user=self.user1,
                user_service=self.user_service,
            )
        self.assertEqual(cm.exception.status_code, 400)
        self.assertEqual(cm.exception.detail, "新密码长度不能少于6位")


if __name__ == "__main__":
    unittest.main()
