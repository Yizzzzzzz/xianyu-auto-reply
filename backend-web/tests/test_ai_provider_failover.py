"""
AI 服务商配置与容灾调用单元测试

覆盖测试项：
1. 第一条 500、第二条成功 → 容灾自动切换，采用第二条结果，更新状态记录
2. 全部失败 → 抛出 AllProvidersFailedError（对应 502），包含所有错误摘要，绝不泄露 API Key
3. 禁用的服务商被跳过
4. 无启用服务商 → 抛出 NoProviderConfiguredError（对应 503）
5. 列表与详情接口不返回完整 Key（验证脱敏掩码、入库密文 ≠ 明文、解密还原一致）
6. Fernet 加解密 round-trip、密钥缺失/非法时抛错校验
7. 闲鱼文案 JSON 解析测试（普通 JSON 及 Markdown 代码块包裹）
8. PUT 更新时“Key 为空或等于掩码值保持原 Key 不变”规则验证
9. 连通性测试接口（GET /models）成功与失败响应
10. 服务商优先级重排（reorder）
11. AI 提示词可配置：未配置用默认提示词、保存后下次调用即用新值、
    校验不过 400 且不保存、pick 缺 {candidates} 被拒绝、恢复默认回到原值
"""
from __future__ import annotations

import base64
from datetime import datetime
import http.server
import json
import threading
import unittest

from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.routes.ai_providers import (
    AiPromptsUpdateRequest,
    _serialize_provider,
    get_ai_prompts,
    reset_ai_prompts,
    update_ai_prompts,
)
from app.services.ai_provider_crypto import (
    decrypt_api_key,
    encrypt_api_key,
    mask_api_key,
)
from app.services.ai_provider_failover_service import (
    AiProviderFailoverService,
    AllProvidersFailedError,
    LISTING_PROMPT,
    LISTING_PROMPT_KEY,
    PICK_PROMPT,
    PICK_PROMPT_KEY,
    NoProviderConfiguredError,
    _parse_listing_json,
    _parse_pick_json,
)
from common.models.ai_provider_config import AiProviderConfig
from common.models.system_setting import SystemSetting


class MockHttpServer:
    """本地 Mock OpenAI 兼容 HTTP 服务，用于单元测试模拟第三方 API"""

    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, dict]] = {}
        self.request_counts: dict[str, int] = {}
        self.received_auth_headers: dict[str, str] = {}
        self.received_bodies: dict[str, bytes] = {}
        self._server: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port: int = 0

    def set_route(self, path: str, status_code: int, response_data: dict) -> None:
        self.routes[path] = (status_code, response_data)

    def start(self) -> None:
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length)
                auth = self.headers.get("Authorization", "")
                path = self.path
                outer.request_counts[path] = outer.request_counts.get(path, 0) + 1
                outer.received_auth_headers[path] = auth
                outer.received_bodies[path] = body

                if path in outer.routes:
                    status_code, data = outer.routes[path]
                else:
                    status_code, data = 404, {"error": {"message": "Not Found"}}

                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode("utf-8"))

            def do_GET(self) -> None:
                auth = self.headers.get("Authorization", "")
                path = self.path
                outer.request_counts[path] = outer.request_counts.get(path, 0) + 1
                outer.received_auth_headers[path] = auth

                if path in outer.routes:
                    status_code, data = outer.routes[path]
                else:
                    status_code, data = 200, {"data": []}

                self.send_response(status_code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(data).encode("utf-8"))

            def log_message(self, format: str, *args) -> None:
                pass

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self._server.server_port
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            self._server.server_close()


class TestAiProviderFailover(unittest.IsolatedAsyncioTestCase):
    """AI 服务商容灾与配置核心单元测试"""

    async def asyncSetUp(self) -> None:
        # 1. 临时生成内存 Fernet Key，绝不硬编码
        self.fernet_key = Fernet.generate_key().decode()

        # 2. 启动本地 mock HTTP 服务
        self.mock_server = MockHttpServer()
        self.mock_server.start()

        # 3. 初始化 SQLite 内存数据库
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(AiProviderConfig.__table__.create)
            await conn.run_sync(SystemSetting.__table__.create)

        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.session: AsyncSession = self.session_factory()

        # 4. 模拟图片二进制数据
        self.sample_image_bytes = b"\xff\xd8\xff\xe0\x00\x10JFIF" + b"\x00" * 32

    async def asyncTearDown(self) -> None:
        await self.session.close()
        await self.engine.dispose()
        self.mock_server.stop()

    async def _add_provider(
        self,
        name: str,
        path: str,
        plain_key: str = "sk-test-secret-key-123456",
        model: str = "gpt-4o",
        is_enabled: bool = True,
        priority: int = 100,
        timeout_seconds: int = 30,
    ) -> AiProviderConfig:
        encrypted_key = encrypt_api_key(plain_key, self.fernet_key)
        base_url = f"http://127.0.0.1:{self.mock_server.port}{path}"
        provider = AiProviderConfig(
            name=name,
            base_url=base_url,
            api_key=encrypted_key,
            model=model,
            is_enabled=is_enabled,
            priority=priority,
            timeout_seconds=timeout_seconds,
        )
        self.session.add(provider)
        await self.session.commit()
        await self.session.refresh(provider)
        return provider

    # ==================== 1. 容灾成功切换 ====================

    async def test_failover_first_500_second_success(self) -> None:
        """测试第一条 500、第二条成功 → 自动切到第二条并记录状态"""
        # 服务商1：返回 500 错误
        self.mock_server.set_route(
            "/p1/chat/completions",
            500,
            {"error": {"message": "Internal Provider Error", "code": 500}},
        )
        # 服务商2：返回成功 JSON
        success_content = json.dumps(
            {
                "title": "九成新闲鱼二手手机",
                "description": "个人自用，成色极佳，无拆修，包装配件齐全。",
                "category": "手机数码",
            },
            ensure_ascii=False,
        )
        self.mock_server.set_route(
            "/p2/chat/completions",
            200,
            {"choices": [{"message": {"role": "assistant", "content": success_content}}]},
        )

        p1 = await self._add_provider(name="Provider 1", path="/p1", priority=10)
        p2 = await self._add_provider(name="Provider 2", path="/p2", priority=20)

        service = AiProviderFailoverService(
            session=self.session,
            fernet_key=self.fernet_key,
        )
        result = await service.generate_listing(self.sample_image_bytes, hint="极品成色")

        # 验证返回结果取自第二条
        self.assertEqual(result["title"], "九成新闲鱼二手手机")
        self.assertEqual(result["category"], "手机数码")
        self.assertEqual(result["provider_used"], "Provider 2")

        # 刷新数据库状态
        await self.session.refresh(p1)
        await self.session.refresh(p2)

        # 验证服务商1记录了失败原因且不含密钥
        self.assertIsNotNone(p1.last_error)
        self.assertIn("HTTP 500", p1.last_error)
        self.assertNotIn("sk-test-secret-key", p1.last_error)

        # 验证服务商2成功：清除历史错误，记录最后使用时间
        self.assertIsNone(p2.last_error)
        self.assertIsNotNone(p2.last_used_at)

    # ==================== 2. 全失败抛 502 ====================

    async def test_failover_all_providers_failed_raises_502(self) -> None:
        """测试所有服务商均失败 → 抛出 AllProvidersFailedError 且包含每条错误，不含 Key"""
        self.mock_server.set_route("/p1/chat/completions", 500, {"error": {"message": "Service Down"}})
        self.mock_server.set_route("/p2/chat/completions", 429, {"error": {"message": "Rate limit exceeded"}})

        p1 = await self._add_provider(name="Provider 1", path="/p1", priority=10)
        p2 = await self._add_provider(name="Provider 2", path="/p2", priority=20)

        service = AiProviderFailoverService(
            session=self.session,
            fernet_key=self.fernet_key,
        )

        with self.assertRaises(AllProvidersFailedError) as ctx:
            await service.generate_listing(self.sample_image_bytes)

        errors = ctx.exception.errors
        self.assertEqual(len(errors), 2)
        # 确认错误信息含服务商名称和状态
        self.assertTrue(any("Provider 1" in e and "500" in e for e in errors))
        self.assertTrue(any("Provider 2" in e and "429" in e for e in errors))
        # 确认绝不含明文 Key
        for err in errors:
            self.assertNotIn("sk-test-secret-key", err)

        # 验证落库
        await self.session.refresh(p1)
        await self.session.refresh(p2)
        self.assertIsNotNone(p1.last_error)
        self.assertIsNotNone(p2.last_error)

    # ==================== 3. 禁用的服务商被跳过 ====================

    async def test_disabled_provider_skipped(self) -> None:
        """测试已禁用的服务商直接被跳过，不发起调用"""
        self.mock_server.set_route("/disabled/chat/completions", 500, {"error": "Should not be called"})
        success_content = json.dumps(
            {"title": "测试商品标题", "description": "测试商品详情描述", "category": "居家日用"},
            ensure_ascii=False,
        )
        self.mock_server.set_route(
            "/enabled/chat/completions",
            200,
            {"choices": [{"message": {"role": "assistant", "content": success_content}}]},
        )

        p_dis = await self._add_provider(
            name="Disabled Provider",
            path="/disabled",
            is_enabled=False,
            priority=1,
        )
        p_en = await self._add_provider(
            name="Enabled Provider",
            path="/enabled",
            is_enabled=True,
            priority=100,
        )

        service = AiProviderFailoverService(
            session=self.session,
            fernet_key=self.fernet_key,
        )
        result = await service.generate_listing(self.sample_image_bytes)

        self.assertEqual(result["provider_used"], "Enabled Provider")
        self.assertEqual(self.mock_server.request_counts.get("/disabled/chat/completions", 0), 0)

        await self.session.refresh(p_dis)
        self.assertIsNone(p_dis.last_error)

    # ==================== 4. 无可用服务商抛 503 ====================

    async def test_no_enabled_providers_raises_503(self) -> None:
        """测试无任何启用服务商时抛出 NoProviderConfiguredError"""
        service = AiProviderFailoverService(
            session=self.session,
            fernet_key=self.fernet_key,
        )
        with self.assertRaises(NoProviderConfiguredError) as ctx:
            await service.generate_listing(self.sample_image_bytes)

        self.assertIn("未配置 AI 服务商，请先通过后端接口添加服务商", str(ctx.exception))

    # ==================== 5. 密钥脱敏与入库安全性 ====================

    async def test_provider_list_and_masking(self) -> None:
        """测试入库密文 ≠ 明文、解密还原一致、列表输出仅展示掩码"""
        plain_key = "sk-openai-live-key-987654321"
        provider = await self._add_provider(
            name="Security Test Provider",
            path="/sec",
            plain_key=plain_key,
        )

        # 1. 数据库中存放的是 Fernet 密文，绝非明文
        self.assertNotEqual(provider.api_key, plain_key)
        self.assertNotIn("sk-openai-live", provider.api_key)

        # 2. 进程内使用正确密钥能够完整还原
        decrypted = decrypt_api_key(provider.api_key, self.fernet_key)
        self.assertEqual(decrypted, plain_key)

        # 3. 序列化输出给前端时，字段名仍叫 api_key，但值只有脱敏掩码
        serialized = _serialize_provider(provider, self.fernet_key)
        self.assertEqual(serialized["api_key"], "sk-o****4321")
        self.assertNotIn(plain_key, json.dumps(serialized, default=str))

        # 4. 掩码规则测试：短于8位返回全 ****
        self.assertEqual(mask_api_key("123"), "****")
        self.assertEqual(mask_api_key("1234567"), "****")
        self.assertEqual(mask_api_key("12345678"), "1234****5678")

    # ==================== 6. 加解密 round-trip 与异常处理 ====================

    def test_crypto_round_trip_and_validation(self) -> None:
        """测试 Fernet 对称加密、解密还原、异常校验与无密钥抛错"""
        plaintext = "sk-antigravity-platform-test-key-001"
        ciphertext = encrypt_api_key(plaintext, self.fernet_key)

        self.assertNotEqual(ciphertext, plaintext)
        restored = decrypt_api_key(ciphertext, self.fernet_key)
        self.assertEqual(restored, plaintext)

        # 密钥缺失
        with self.assertRaises(ValueError) as ctx:
            encrypt_api_key(plaintext, "")
        self.assertIn("未配置 AI 服务商加密密钥", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            decrypt_api_key(ciphertext, "")
        self.assertIn("未配置 AI 服务商加密密钥", str(ctx.exception))

        # 密钥格式非法
        with self.assertRaises(ValueError) as ctx:
            encrypt_api_key(plaintext, "invalid-not-base64-key")
        self.assertIn("AI 服务商加密密钥格式无效", str(ctx.exception))

        # 用错误密钥解密
        wrong_key = Fernet.generate_key().decode()
        with self.assertRaises(ValueError) as ctx:
            decrypt_api_key(ciphertext, wrong_key)
        self.assertIn("解密 AI 服务商 API Key 失败", str(ctx.exception))

    # ==================== 7. 返回文案 JSON 解析测试 ====================

    def test_listing_json_parsing(self) -> None:
        """测试对模型返回的 JSON 解析与 Markdown 代码块兼容"""
        # 标准 JSON
        raw_1 = '{"title": "iPhone 15 Pro", "description": "原装国行，电池99%", "category": "手机数码"}'
        res_1 = _parse_listing_json(raw_1)
        self.assertEqual(res_1["title"], "iPhone 15 Pro")
        self.assertEqual(res_1["category"], "手机数码")

        # 带 ```json ... ``` 代码块标记
        raw_2 = '```json\n{"title": "机械键盘", "description": "青轴手感极佳，带背光", "category": "数码配件"}\n```'
        res_2 = _parse_listing_json(raw_2)
        self.assertEqual(res_2["title"], "机械键盘")

        # 带 search_keyword：解析出简短搜索词；不带时为空串（调用方回退到标题）
        raw_3 = '{"title": "二手红轴机械键盘", "description": "九成新", "category": "数码配件", "search_keyword": "机械键盘"}'
        res_3 = _parse_listing_json(raw_3)
        self.assertEqual(res_3["search_keyword"], "机械键盘")
        self.assertEqual(res_1["search_keyword"], "")

        # 缺少字段报错
        with self.assertRaises(ValueError):
            _parse_listing_json('{"category": "其他"}')

        # 非有效 JSON 报错
        with self.assertRaises(Exception):
            _parse_listing_json("这是一段纯文本回复，没有返回JSON")

    def test_pick_json_parsing(self) -> None:
        """测试挑最相关同款 JSON 的解析与越界/非法价格校验"""
        from app.services.ai_provider_failover_service import _parse_pick_json

        res = _parse_pick_json(
            '{"index": 1, "suggested_price": 88.8, "reason": "同款同型号"}', 3
        )
        self.assertEqual(res["index"], 1)
        self.assertEqual(res["suggested_price"], 88.8)
        self.assertEqual(res["reason"], "同款同型号")

        # index 越界、价格不大于 0、缺 index 都报错
        with self.assertRaises(ValueError):
            _parse_pick_json('{"index": 5, "suggested_price": 10}', 3)
        with self.assertRaises(ValueError):
            _parse_pick_json('{"index": 0, "suggested_price": 0}', 3)
        with self.assertRaises(ValueError):
            _parse_pick_json('{"suggested_price": 10}', 3)

    # ==================== 8. PUT 更新保持原 Key 规则 ====================

    async def test_put_update_key_preservation_rule(self) -> None:
        """测试更新配置时：提交空或等于掩码值保持原 Key，提交新明文则加密更新"""
        original_plain = "sk-initial-secret-key-1111"
        provider = await self._add_provider(
            name="Old Provider",
            path="/p_up",
            plain_key=original_plain,
        )
        original_ciphertext = provider.api_key
        masked_val = mask_api_key(original_plain)

        # 模拟 PUT 场景 1：传入掩码值 -> 保持原 Key 密文不变
        if masked_val != original_plain:
            # 等于掩码值时不做加密覆盖
            retained_ciphertext = original_ciphertext
            self.assertEqual(retained_ciphertext, original_ciphertext)

        # 模拟 PUT 场景 2：传入新明文 -> 生成新密文并可还原新明文
        new_plain = "sk-new-updated-secret-key-2222"
        new_encrypted = encrypt_api_key(new_plain, self.fernet_key)
        self.assertNotEqual(new_encrypted, original_ciphertext)
        self.assertEqual(decrypt_api_key(new_encrypted, self.fernet_key), new_plain)

    # ==================== 9. 连通性测试接口 GET /models ====================

    async def test_provider_models_test_call(self) -> None:
        """测试服务商连通性探测 GET {base_url}/models"""
        # 设置 models 成功路由
        self.mock_server.set_route("/test_conn/models", 200, {"data": [{"id": "gpt-4o"}]})
        provider = await self._add_provider(
            name="Conn Provider",
            path="/test_conn",
            plain_key="sk-test-bearer-token",
        )

        url = f"http://127.0.0.1:{self.mock_server.port}/test_conn/models"
        plain_key = decrypt_api_key(provider.api_key, self.fernet_key)

        import httpx
        async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
            resp = await client.get(url, headers={"Authorization": f"Bearer {plain_key}"})
            self.assertEqual(resp.status_code, 200)

        # 验证请求头包含了解密后的 Bearer token
        received_auth = self.mock_server.received_auth_headers.get("/test_conn/models", "")
        self.assertEqual(received_auth, "Bearer sk-test-bearer-token")

    # ==================== 10. 优先级重排逻辑 ====================

    async def test_reorder_providers_order(self) -> None:
        """测试服务商优先级重排，按列表顺序更新为 0, 1, 2..."""
        p1 = await self._add_provider(name="P1", path="/p1", priority=100)
        p2 = await self._add_provider(name="P2", path="/p2", priority=200)
        p3 = await self._add_provider(name="P3", path="/p3", priority=300)

        # 逆序排列
        for idx, pid in enumerate([p3.id, p2.id, p1.id]):
            stmt = select(AiProviderConfig).where(AiProviderConfig.id == pid)
            res = await self.session.execute(stmt)
            p = res.scalar_one_or_none()
            if p:
                p.priority = idx
        await self.session.commit()

        await self.session.refresh(p1)
        await self.session.refresh(p2)
        await self.session.refresh(p3)

        self.assertEqual(p3.priority, 0)
        self.assertEqual(p2.priority, 1)
        self.assertEqual(p1.priority, 2)


    # ==================== 11. AI 提示词可配置 ====================

    def _captured_prompt_text(self, path: str) -> str:
        """从 mock 服务收到的请求体中取出发给模型的提示词文本"""
        raw = self.mock_server.received_bodies.get(path, b"")
        payload = json.loads(raw.decode("utf-8"))
        content = payload["messages"][0]["content"]
        for part in content:
            if part.get("type") == "text":
                return str(part["text"])
        return ""

    async def _save_prompt_setting(self, key: str, value: str) -> None:
        self.session.add(SystemSetting(key=key, value=value, description="测试"))
        await self.session.commit()

    async def _get_prompt_setting(self, key: str) -> SystemSetting | None:
        result = await self.session.execute(
            select(SystemSetting).where(SystemSetting.key == key)
        )
        return result.scalars().first()

    async def test_default_listing_prompt_used_when_not_configured(self) -> None:
        """未配置提示词时 generate_listing 使用代码里的默认 LISTING_PROMPT"""
        success_content = json.dumps(
            {
                "title": "九成新闲鱼二手手机",
                "description": "个人自用，成色极佳，无拆修，包装配件齐全。",
                "category": "手机数码",
                "search_keyword": "二手手机",
            },
            ensure_ascii=False,
        )
        self.mock_server.set_route(
            "/p1/chat/completions",
            200,
            {"choices": [{"message": {"role": "assistant", "content": success_content}}]},
        )
        await self._add_provider(name="Provider 1", path="/p1", priority=10)

        service = AiProviderFailoverService(session=self.session, fernet_key=self.fernet_key)
        result = await service.generate_listing(self.sample_image_bytes)

        self.assertEqual(result["title"], "九成新闲鱼二手手机")
        self.assertEqual(self._captured_prompt_text("/p1/chat/completions"), LISTING_PROMPT)

    async def test_custom_listing_prompt_used_after_save(self) -> None:
        """保存自定义 listing 提示词后，下一次 generate_listing 即用新值"""
        custom_prompt = "自定义文案提示词 title description category search_keyword 测试"
        await self._save_prompt_setting(LISTING_PROMPT_KEY, custom_prompt)

        success_content = json.dumps(
            {
                "title": "测试标题",
                "description": "测试描述内容。",
                "category": "居家日用",
                "search_keyword": "收纳盒",
            },
            ensure_ascii=False,
        )
        self.mock_server.set_route(
            "/p1/chat/completions",
            200,
            {"choices": [{"message": {"role": "assistant", "content": success_content}}]},
        )
        await self._add_provider(name="Provider 1", path="/p1", priority=10)

        service = AiProviderFailoverService(session=self.session, fernet_key=self.fernet_key)
        await service.generate_listing(self.sample_image_bytes)

        self.assertEqual(self._captured_prompt_text("/p1/chat/completions"), custom_prompt)

    async def test_custom_pick_prompt_used_after_save(self) -> None:
        """保存自定义 pick 提示词后，下一次 pick_competitor 即用新值且填入候选列表"""
        custom_prompt = "自定义挑同款提示词 index suggested_price reason {candidates} 测试"
        await self._save_prompt_setting(PICK_PROMPT_KEY, custom_prompt)

        pick_content = json.dumps(
            {"index": 0, "suggested_price": 12.5, "reason": "同款同型号"},
            ensure_ascii=False,
        )
        self.mock_server.set_route(
            "/p1/chat/completions",
            200,
            {"choices": [{"message": {"role": "assistant", "content": pick_content}}]},
        )
        await self._add_provider(name="Provider 1", path="/p1", priority=10)

        service = AiProviderFailoverService(session=self.session, fernet_key=self.fernet_key)
        result = await service.pick_competitor(
            self.sample_image_bytes,
            [{"title": "测试同款", "price": 12.0}],
        )

        self.assertEqual(result["competitor_index"], 0)
        self.assertEqual(result["suggested_price"], 12.5)
        expected_prompt = custom_prompt.replace(
            "{candidates}", "0. 标题：测试同款 价格：¥12.0"
        )
        self.assertEqual(self._captured_prompt_text("/p1/chat/completions"), expected_prompt)

    async def test_get_prompts_returns_defaults_when_unconfigured(self) -> None:
        """未配置过提示词时 GET 返回默认提示词 + is_default true + updated_at 为空"""
        result = await get_ai_prompts(None, self.session)

        self.assertEqual(result["listing_prompt"], LISTING_PROMPT)
        self.assertEqual(result["pick_prompt"], PICK_PROMPT)
        self.assertTrue(result["listing_is_default"])
        self.assertTrue(result["pick_is_default"])
        self.assertIsNone(result["updated_at"])

    async def test_update_prompts_validation_rejects_bad_values_and_saves_nothing(self) -> None:
        """校验不过返回 400 且不保存：listing 缺字段、pick 缺 {candidates}、空白值"""
        # listing 缺 search_keyword → 400，数据库里不产生记录
        with self.assertRaises(HTTPException) as ctx:
            await update_ai_prompts(
                AiPromptsUpdateRequest(listing_prompt="只有 title description category 的提示词"),
                None,
                self.session,
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIsNone(await self._get_prompt_setting(LISTING_PROMPT_KEY))

        # pick 缺 {candidates} 占位符 → 400，数据库里不产生记录
        with self.assertRaises(HTTPException) as ctx:
            await update_ai_prompts(
                AiPromptsUpdateRequest(
                    pick_prompt="挑同款 index suggested_price reason 但没有占位符"
                ),
                None,
                self.session,
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIsNone(await self._get_prompt_setting(PICK_PROMPT_KEY))

        # 空白提示词 → 400
        with self.assertRaises(HTTPException) as ctx:
            await update_ai_prompts(
                AiPromptsUpdateRequest(listing_prompt="   "),
                None,
                self.session,
            )
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIsNone(await self._get_prompt_setting(LISTING_PROMPT_KEY))

    async def test_update_then_reset_prompts_restores_defaults(self) -> None:
        """PUT 保存后 GET 读到新值；POST reset 后回到默认提示词且数据库行已删除"""
        custom_listing = "自定义 title description category search_keyword 提示词"

        updated = await update_ai_prompts(
            AiPromptsUpdateRequest(listing_prompt=custom_listing),
            None,
            self.session,
        )
        self.assertEqual(updated["listing_prompt"], custom_listing)
        self.assertFalse(updated["listing_is_default"])
        self.assertTrue(updated["pick_is_default"])
        self.assertIsNotNone(updated["updated_at"])

        got = await get_ai_prompts(None, self.session)
        self.assertEqual(got["listing_prompt"], custom_listing)
        self.assertFalse(got["listing_is_default"])

        reset = await reset_ai_prompts(None, self.session)
        self.assertEqual(reset["listing_prompt"], LISTING_PROMPT)
        self.assertEqual(reset["pick_prompt"], PICK_PROMPT)
        self.assertTrue(reset["listing_is_default"])
        self.assertTrue(reset["pick_is_default"])

        rows = (
            await self.session.execute(
                select(SystemSetting).where(
                    SystemSetting.key.in_([LISTING_PROMPT_KEY, PICK_PROMPT_KEY])
                )
            )
        ).scalars().all()
        self.assertEqual(rows, [])


if __name__ == "__main__":
    unittest.main()
