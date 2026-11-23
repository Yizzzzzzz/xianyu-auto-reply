"""
AI 对话追问后续图片契约与容灾单元测试

覆盖测试项：
1. 归一化单测：
   - 纯文字历史原样通过
   - 含一张图的 vision 消息通过
   - 未知 part 类型报 ValueError
   - image_url 缺 url 报 ValueError
   - base64 解不出来报 ValueError
   - 单张超 8MB 报 ValueError
   - 4 张图的历史里最旧一张被替换为 "[图片]" 占位、最新 3 张保留
   - assistant 发列表内容报 ValueError
   - 幂等（归一化两次结果相同）
2. 服务层测试（MockHttpServer）：
   - chat 带图片消息时发给服务商的 payload 里 messages 含 image_url part 且能拿到 reply
   - chat_stream 带图片消息时正常出增量与 done 事件
3. 路由层测试：
   - POST /ai/chat 发非法 part 时返回 400
   - POST /ai/chat/stream 发非法 part 时返回 400
"""
from __future__ import annotations

import base64
import http.server
import json
import sys
import threading
import unittest
from unittest.mock import AsyncMock, MagicMock

for _mod in ("cv2", "paramiko"):
    sys.modules.setdefault(_mod, MagicMock())

from cryptography.fernet import Fernet
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.api.routes.ai_providers import chat_endpoint, chat_stream_endpoint
from app.services.ai_provider_crypto import encrypt_api_key
from app.services.ai_provider_failover_service import (
    AiProviderFailoverService,
    _normalize_chat_messages,
)
from common.models.ai_provider_config import AiProviderConfig
from common.models.system_setting import SystemSetting


class TestNormalizeChatMessages(unittest.TestCase):
    """对话消息归一化与契约校验单测"""

    def setUp(self) -> None:
        self.sample_b64 = base64.b64encode(b"fake_jpeg_image_binary_content").decode("utf-8")
        self.valid_data_url = f"data:image/jpeg;base64,{self.sample_b64}"

    def test_pure_text_messages_pass(self) -> None:
        """纯文字历史原样通过"""
        raw = [
            {"role": "user", "content": "你好，这是什么东西？"},
            {"role": "assistant", "content": "这是一个机械键盘。"},
            {"role": "user", "content": "那手感怎么样？"},
        ]
        result = _normalize_chat_messages(raw)
        self.assertEqual(len(result), 3)
        self.assertEqual(result[0]["role"], "user")
        self.assertEqual(result[0]["content"], "你好，这是什么东西？")
        self.assertEqual(result[1]["role"], "assistant")
        self.assertEqual(result[1]["content"], "这是一个机械键盘。")
        self.assertEqual(result[2]["role"], "user")
        self.assertEqual(result[2]["content"], "那手感怎么样？")

    def test_vision_message_with_one_image_pass(self) -> None:
        """含一张图的 vision 消息通过（支持 data URL 与 http URL）"""
        raw = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "看看这个"},
                    {"type": "image_url", "image_url": {"url": self.valid_data_url}},
                ],
            }
        ]
        result = _normalize_chat_messages(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(len(result[0]["content"]), 2)
        self.assertEqual(result[0]["content"][0]["type"], "text")
        self.assertEqual(result[0]["content"][0]["text"], "看看这个")
        self.assertEqual(result[0]["content"][1]["type"], "image_url")
        self.assertEqual(result[0]["content"][1]["image_url"]["url"], self.valid_data_url)

        # http(s) URL 也支持
        raw_http = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "https://example.com/pic.jpg"}}
                ],
            }
        ]
        res_http = _normalize_chat_messages(raw_http)
        self.assertEqual(res_http[0]["content"][0]["image_url"]["url"], "https://example.com/pic.jpg")

    def test_unknown_part_type_raises_value_error(self) -> None:
        """未知 part 类型报 ValueError"""
        raw = [
            {
                "role": "user",
                "content": [
                    {"type": "audio", "audio": "http://example.com/voice.mp3"}
                ],
            }
        ]
        with self.assertRaises(ValueError) as ctx:
            _normalize_chat_messages(raw)
        self.assertIn("不支持的类型", str(ctx.exception))

    def test_image_url_missing_url_raises_value_error(self) -> None:
        """image_url 缺 url 报 ValueError"""
        raw_no_url = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {}}
                ],
            }
        ]
        with self.assertRaises(ValueError) as ctx:
            _normalize_chat_messages(raw_no_url)
        self.assertIn("缺少有效的 url 字段", str(ctx.exception))

        raw_no_obj = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url"}
                ],
            }
        ]
        with self.assertRaises(ValueError) as ctx2:
            _normalize_chat_messages(raw_no_obj)
        self.assertIn("缺少 image_url 对象", str(ctx2.exception))

    def test_invalid_base64_raises_value_error(self) -> None:
        """base64 解不出来报 ValueError"""
        raw = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,!!!invalid_base64_content!!!"}}
                ],
            }
        ]
        with self.assertRaises(ValueError) as ctx:
            _normalize_chat_messages(raw)
        self.assertIn("Base64 解码失败", str(ctx.exception))

    def test_image_exceeds_8mb_raises_value_error(self) -> None:
        """单张超 8MB 报 ValueError"""
        large_bytes = b"0" * (8 * 1024 * 1024 + 1)
        large_b64 = base64.b64encode(large_bytes).decode("utf-8")
        raw = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{large_b64}"}}
                ],
            }
        ]
        with self.assertRaises(ValueError) as ctx:
            _normalize_chat_messages(raw)
        self.assertIn("超过 8MB 限制", str(ctx.exception))

    def test_four_images_oldest_replaced_with_placeholder_and_latest_three_kept(self) -> None:
        """4 张图的历史里最旧一张被替换为 "[图片]" 占位、最新 3 张保留"""
        img1 = f"data:image/jpeg;base64,{base64.b64encode(b'img1').decode()}"
        img2 = f"data:image/png;base64,{base64.b64encode(b'img2').decode()}"
        img3 = f"data:image/webp;base64,{base64.b64encode(b'img3').decode()}"
        img4 = f"data:image/jpeg;base64,{base64.b64encode(b'img4').decode()}"

        raw = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "第一张"},
                    {"type": "image_url", "image_url": {"url": img1}},
                ],
            },
            {"role": "assistant", "content": "收到第一张。"},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": img2}},
                ],
            },
            {"role": "assistant", "content": "收到第二张。"},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": img3}},
                    {"type": "image_url", "image_url": {"url": img4}},
                ],
            },
        ]

        result = _normalize_chat_messages(raw)

        # 检查轮次结构未丢：总共 5 条消息
        self.assertEqual(len(result), 5)

        # 第 1 条消息中的 img1 应该被替换为 text: "[图片]"
        self.assertEqual(result[0]["content"][0], {"type": "text", "text": "第一张"})
        self.assertEqual(result[0]["content"][1], {"type": "text", "text": "[图片]"})

        # 第 2 条保留助手文本
        self.assertEqual(result[1]["content"], "收到第一张。")

        # 第 3 条中的 img2 保留
        self.assertEqual(result[2]["content"][0]["type"], "image_url")
        self.assertEqual(result[2]["content"][0]["image_url"]["url"], img2)

        # 第 4 条保留助手文本
        self.assertEqual(result[3]["content"], "收到第二张。")

        # 第 5 条中的 img3, img4 保留
        self.assertEqual(result[4]["content"][0]["type"], "image_url")
        self.assertEqual(result[4]["content"][0]["image_url"]["url"], img3)
        self.assertEqual(result[4]["content"][1]["type"], "image_url")
        self.assertEqual(result[4]["content"][1]["image_url"]["url"], img4)

    def test_assistant_list_content_raises_value_error(self) -> None:
        """assistant 发列表内容报 ValueError"""
        raw = [
            {
                "role": "assistant",
                "content": [{"type": "text", "text": "我是助手"}],
            }
        ]
        with self.assertRaises(ValueError) as ctx:
            _normalize_chat_messages(raw)
        self.assertIn("assistant 消息内容不能为列表", str(ctx.exception))

    def test_normalize_idempotent(self) -> None:
        """幂等（归一化两次结果相同）"""
        img1 = f"data:image/jpeg;base64,{base64.b64encode(b'img1').decode()}"
        img2 = f"data:image/png;base64,{base64.b64encode(b'img2').decode()}"
        img3 = f"data:image/webp;base64,{base64.b64encode(b'img3').decode()}"
        img4 = f"data:image/jpeg;base64,{base64.b64encode(b'img4').decode()}"

        raw = [
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": img1}}]},
            {"role": "assistant", "content": "收到1"},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": img2}}]},
            {"role": "assistant", "content": "收到2"},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": img3}}]},
            {"role": "assistant", "content": "收到3"},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": img4}}]},
        ]

        first_pass = _normalize_chat_messages(raw)
        second_pass = _normalize_chat_messages(first_pass)

        self.assertEqual(first_pass, second_pass)


class MockChatHttpServer:
    """本地 Mock OpenAI 兼容 HTTP 服务"""

    def __init__(self) -> None:
        self.routes: dict[str, tuple[int, dict]] = {}
        self.stream_routes: dict[str, tuple[int, str]] = {}
        self.received_bodies: dict[str, bytes] = {}
        self._server: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port: int = 0

    def set_route(self, path: str, status_code: int, response_data: dict) -> None:
        self.routes[path] = (status_code, response_data)

    def set_stream_route(self, path: str, status_code: int, sse_text: str) -> None:
        self.stream_routes[path] = (status_code, sse_text)

    def start(self) -> None:
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                content_length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(content_length)
                path = self.path
                outer.received_bodies[path] = body

                if path in outer.stream_routes:
                    status_code, sse_text = outer.stream_routes[path]
                    self.send_response(status_code)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.end_headers()
                    self.wfile.write(sse_text.encode("utf-8"))
                    return

                if path in outer.routes:
                    status_code, data = outer.routes[path]
                else:
                    status_code, data = 404, {"error": {"message": "Not Found"}}

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


class TestAiChatImageService(unittest.IsolatedAsyncioTestCase):
    """服务层带图追问与流式测试"""

    async def asyncSetUp(self) -> None:
        self.fernet_key = Fernet.generate_key().decode()
        self.mock_server = MockChatHttpServer()
        self.mock_server.start()

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

    async def asyncTearDown(self) -> None:
        await self.session.close()
        await self.engine.dispose()
        self.mock_server.stop()

    async def _add_provider(
        self,
        name: str,
        path: str,
        plain_key: str = "sk-test-secret-key-123456",
        model: str = "deepseek-v4.1-flash",
    ) -> AiProviderConfig:
        encrypted_key = encrypt_api_key(plain_key, self.fernet_key)
        base_url = f"http://127.0.0.1:{self.mock_server.port}{path}"
        provider = AiProviderConfig(
            name=name,
            base_url=base_url,
            api_key=encrypted_key,
            model=model,
            is_enabled=True,
            priority=10,
            timeout_seconds=30,
        )
        self.session.add(provider)
        await self.session.commit()
        await self.session.refresh(provider)
        return provider

    async def test_chat_with_image_payload_and_reply(self) -> None:
        """chat 带图片消息时发给服务商的 payload 里 messages 含 image_url part 且能拿到 reply"""
        reply_text = "从图中看，这是一个罗技 G304 鼠标。"
        self.mock_server.set_route(
            "/p1/chat/completions",
            200,
            {"choices": [{"message": {"role": "assistant", "content": reply_text}}]},
        )
        await self._add_provider(name="Provider 1", path="/p1")

        service = AiProviderFailoverService(session=self.session, fernet_key=self.fernet_key)
        sample_b64 = base64.b64encode(b"mouse_img").decode()
        data_url = f"data:image/jpeg;base64,{sample_b64}"

        history = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "看看这是啥？"},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ]

        res = await service.chat(messages=history)
        self.assertEqual(res["reply"], reply_text)
        self.assertEqual(res["provider_used"], "Provider 1")

        # 检查发往 upstream 的 payload
        raw = self.mock_server.received_bodies.get("/p1/chat/completions", b"")
        sent = json.loads(raw.decode("utf-8"))
        sent_messages = sent["messages"]
        self.assertEqual(sent_messages[0]["role"], "system")
        self.assertEqual(sent_messages[1]["role"], "user")
        self.assertEqual(sent_messages[1]["content"][0]["type"], "text")
        self.assertEqual(sent_messages[1]["content"][1]["type"], "image_url")
        self.assertEqual(sent_messages[1]["content"][1]["image_url"]["url"], data_url)

    async def test_chat_stream_with_image_incremental_and_done(self) -> None:
        """chat_stream 带图片消息时正常出增量与 done 事件"""
        stream_body = (
            'data: {"choices": [{"delta": {"content": "这是"}}]}\n\n'
            'data: {"choices": [{"delta": {"content": "一个鼠标。"}}]}\n\n'
            'data: [DONE]\n\n'
        )
        self.mock_server.set_stream_route("/p1/chat/completions", 200, stream_body)
        await self._add_provider(name="Provider 1", path="/p1")

        service = AiProviderFailoverService(session=self.session, fernet_key=self.fernet_key)
        sample_b64 = base64.b64encode(b"mouse_img").decode()
        data_url = f"data:image/jpeg;base64,{sample_b64}"

        history = [
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": data_url}},
                    {"type": "text", "text": "请介绍一下"},
                ],
            }
        ]

        events = [e async for e in service.chat_stream(history)]
        self.assertEqual(len(events), 3)
        self.assertEqual(events[0], 'data: {"delta": "这是"}\n\n')
        self.assertEqual(events[1], 'data: {"delta": "一个鼠标。"}\n\n')
        self.assertIn("event: done", events[2])
        self.assertIn('"provider_used": "Provider 1"', events[2])


class TestAiChatRoute(unittest.IsolatedAsyncioTestCase):
    """路由层契约校验测试"""

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.session: AsyncSession = self.session_factory()

    async def asyncTearDown(self) -> None:
        await self.session.close()
        await self.engine.dispose()

    async def test_chat_endpoint_invalid_part_returns_400(self) -> None:
        """POST /ai/chat 发非法 part 时返回 400"""
        req = MagicMock()
        req.json = AsyncMock(
            return_value={
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "video", "url": "http://example.com/a.mp4"}
                        ],
                    }
                ]
            }
        )

        with self.assertRaises(HTTPException) as ctx:
            await chat_endpoint(req, None, self.session)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("不支持的类型", str(ctx.exception.detail))

    async def test_chat_stream_endpoint_invalid_part_returns_400(self) -> None:
        """POST /ai/chat/stream 发非法 part 时返回 400（不建流）"""
        req = MagicMock()
        req.json = AsyncMock(
            return_value={
                "messages": [
                    {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "非法列表"}],
                    }
                ]
            }
        )

        with self.assertRaises(HTTPException) as ctx:
            await chat_stream_endpoint(req, None, self.session)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("assistant 消息内容不能为列表", str(ctx.exception.detail))


if __name__ == "__main__":
    unittest.main()
