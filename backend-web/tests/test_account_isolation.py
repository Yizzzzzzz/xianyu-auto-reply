"""
账号数据隔离回归测试集 (Account Isolation Regression Tests)

测试目标：
1. 账号隔离：普通用户（MEMBER角色）只能查询自身关联的闲鱼账号（owner_id == user.id）。
2. 管理员全局视角：管理员（ADMIN角色）owner_id=None，可查询平台全部闲鱼账号。
3. 零账号普通用户：返回空列表，绝不泄露其他用户或管理员账号。
4. 覆盖 Service 层（AccountService）及 Route 层（list_cookie_details）的数据作用域逻辑。
"""
from __future__ import annotations

import unittest
from sqlalchemy import BigInteger, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.api.routes.cookies import list_cookie_details
from app.services.account_service import AccountService
from common.models.user import User, UserRole, UserStatus
from common.models.xy_account import XYAccount
from common.utils.auth_scope import is_admin_user, resolve_owner_scope


# SQLite 兼容：将 BigInteger 编译为 INTEGER，以支持 SQLite 自增主键
@compiles(BigInteger, "sqlite")
def _compile_big_int_sqlite(type_, compiler, **kw):
    return "INTEGER"


class TestAccountIsolation(unittest.IsolatedAsyncioTestCase):
    """闲鱼账号多用户隔离测试"""

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(User.__table__.create)
            await conn.run_sync(XYAccount.__table__.create)
            # 创建消息过滤规则表（list_cookie_details 中关联查询）
            await conn.execute(
                text(
                    "CREATE TABLE IF NOT EXISTS xy_message_filters ("
                    "id INTEGER PRIMARY KEY, "
                    "account_id VARCHAR(80), "
                    "keyword VARCHAR(255), "
                    "filter_type VARCHAR(50), "
                    "enabled INTEGER)"
                )
            )

        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.session: AsyncSession = self.session_factory()
        self.account_service = AccountService(self.session)

        # 准备用户数据
        # 1. 管理员 (owner_id=1)
        self.admin_user = User(
            id=1,
            username="admin_eric",
            password_hash="mock_hash_admin",
            role=UserRole.ADMIN,
            status=UserStatus.ACTIVE,
        )
        # 2. 普通用户 2 (owner_id=2，拥有 1 个闲鱼号)
        self.member_user2 = User(
            id=2,
            username="member_user2",
            password_hash="mock_hash_user2",
            role=UserRole.MEMBER,
            status=UserStatus.ACTIVE,
        )
        # 3. 零账号新用户 3 (owner_id=3，无闲鱼号)
        self.member_empty = User(
            id=3,
            username="test_register_iso1",
            password_hash="mock_hash_user3",
            role=UserRole.MEMBER,
            status=UserStatus.ACTIVE,
        )
        self.session.add_all([self.admin_user, self.member_user2, self.member_empty])

        # 准备闲鱼账号数据
        # 管理员的闲鱼号：xy057799537627
        self.acc_admin = XYAccount(
            id=1,
            owner_id=1,
            account_id="xy057799537627",
            display_name="AdminXYAccount",
            cookie="cookie_data_admin",
            login_method="qr",
            status="active",
            remark="管理员闲鱼号",
        )
        # 用户 2 的闲鱼号：xy_user2_8888
        self.acc_user2 = XYAccount(
            id=2,
            owner_id=2,
            account_id="xy_user2_8888",
            display_name="User2XYAccount",
            cookie="cookie_data_user2",
            login_method="qr",
            status="active",
            remark="用户2闲鱼号",
        )
        self.session.add_all([self.acc_admin, self.acc_user2])
        await self.session.commit()

    async def asyncTearDown(self) -> None:
        await self.session.close()
        async with self.engine.begin() as conn:
            await conn.execute(text("DROP TABLE IF EXISTS xy_message_filters"))
            await conn.run_sync(XYAccount.__table__.drop)
            await conn.run_sync(User.__table__.drop)
        await self.engine.dispose()

    async def test_auth_scope_resolver(self) -> None:
        """测试 auth_scope 解析逻辑：管理员 owner_id=None，普通用户 owner_id=user.id"""
        admin_scope, is_admin = resolve_owner_scope(self.admin_user)
        self.assertIsNone(admin_scope)
        self.assertTrue(is_admin)
        self.assertTrue(is_admin_user(self.admin_user))

        user2_scope, is_user2_admin = resolve_owner_scope(self.member_user2)
        self.assertEqual(user2_scope, 2)
        self.assertFalse(is_user2_admin)
        self.assertFalse(is_admin_user(self.member_user2))

        empty_scope, is_empty_admin = resolve_owner_scope(self.member_empty)
        self.assertEqual(empty_scope, 3)
        self.assertFalse(is_empty_admin)

    async def test_service_list_accounts_isolation(self) -> None:
        """Service 层隔离：list_accounts 仅返回属于指定 owner_id 的账号，None 返回全量"""
        # 用户 2 仅能查询到自己的账号
        accounts_user2 = await self.account_service.list_accounts(owner_id=2)
        self.assertEqual(len(accounts_user2), 1)
        self.assertEqual(accounts_user2[0].account_id, "xy_user2_8888")
        self.assertEqual(accounts_user2[0].owner_id, 2)
        self.assertNotIn("xy057799537627", [a.account_id for a in accounts_user2])

        # 用户 1 仅查询到自己的账号
        accounts_user1 = await self.account_service.list_accounts(owner_id=1)
        self.assertEqual(len(accounts_user1), 1)
        self.assertEqual(accounts_user1[0].account_id, "xy057799537627")
        self.assertEqual(accounts_user1[0].owner_id, 1)

        # 零账号用户 3 查询结果为空列表
        accounts_user3 = await self.account_service.list_accounts(owner_id=3)
        self.assertEqual(accounts_user3, [])

        # 管理员视角 (owner_id=None) 查询到全部账号
        accounts_all = await self.account_service.list_accounts(owner_id=None)
        self.assertEqual(len(accounts_all), 2)
        all_ids = {a.account_id for a in accounts_all}
        self.assertIn("xy057799537627", all_ids)
        self.assertIn("xy_user2_8888", all_ids)

    async def test_service_list_account_ids_isolation(self) -> None:
        """Service 层隔离：list_account_ids 按 owner_id 过滤，无账号用户返回 []"""
        ids_user2 = await self.account_service.list_account_ids(owner_id=2)
        self.assertEqual(ids_user2, ["xy_user2_8888"])

        ids_user1 = await self.account_service.list_account_ids(owner_id=1)
        self.assertEqual(ids_user1, ["xy057799537627"])

        ids_empty = await self.account_service.list_account_ids(owner_id=3)
        self.assertEqual(ids_empty, [])

        ids_all = await self.account_service.list_account_ids(owner_id=None)
        self.assertEqual(sorted(ids_all), ["xy057799537627", "xy_user2_8888"])

    async def test_service_list_account_options_isolation(self) -> None:
        """Service 层隔离：list_account_options 下拉选项按 owner_id 过滤"""
        options_user2 = await self.account_service.list_account_options(owner_id=2)
        self.assertEqual(len(options_user2), 1)
        self.assertEqual(options_user2[0]["id"], "xy_user2_8888")

        options_empty = await self.account_service.list_account_options(owner_id=3)
        self.assertEqual(options_empty, [])

        options_all = await self.account_service.list_account_options(owner_id=None)
        self.assertEqual(len(options_all), 2)

    async def test_route_list_cookie_details_isolation(self) -> None:
        """Route 层路由隔离：GET /api/v1/cookies/details 根据 current_user 严格隔离数据"""
        # 1. 用户 2 请求详情：只包含用户 2 的账号，绝不包含 admin 的 xy057799537627
        details_user2 = await list_cookie_details(
            current_user=self.member_user2,
            account_service=self.account_service,
            session=self.session,
        )
        self.assertEqual(len(details_user2), 1)
        self.assertEqual(details_user2[0].id, "xy_user2_8888")
        self.assertNotIn("xy057799537627", [d.id for d in details_user2])

        # 2. 新注册/无账号用户 3 请求详情：返回空列表 []
        details_empty = await list_cookie_details(
            current_user=self.member_empty,
            account_service=self.account_service,
            session=self.session,
        )
        self.assertEqual(details_empty, [])

        # 3. 管理员请求详情：返回全部账号详情
        details_admin = await list_cookie_details(
            current_user=self.admin_user,
            account_service=self.account_service,
            session=self.session,
        )
        self.assertEqual(len(details_admin), 2)
        admin_seen_ids = {d.id for d in details_admin}
        self.assertIn("xy057799537627", admin_seen_ids)
        self.assertIn("xy_user2_8888", admin_seen_ids)


if __name__ == "__main__":
    unittest.main()
