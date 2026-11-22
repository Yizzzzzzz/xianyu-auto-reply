"""
AI 服务商容灾调用服务

功能：
1. 从数据库读取所有已启用的 AI 服务商配置，按 priority 升序、id 升序逐条轮询尝试。
2. 采用 OpenAI 兼容的 chat completions + vision 协议调用多模态模型生成闲鱼商品文案。
3. 单次调用无内部重试，失败立即记录 last_error 并切至下一服务商；成功则更新 last_used_at 并清空 last_error。
4. 全服务商失败返回 502 并汇总错误，无可用服务商返回 503。
5. 所有依赖（session、fernet_key、http_client）均支持参数注入，便于单元测试。
"""
from __future__ import annotations

import base64
from datetime import datetime
import json
from typing import Any, List, Optional

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.ai_provider_crypto import decrypt_api_key
from common.models.ai_provider_config import AiProviderConfig
from common.services.ai_provider_service import build_openai_url, extract_response_error

# 固定提示词：后端定义，无需前端传入
LISTING_PROMPT = """你是一个专业的闲鱼二手交易平台商品发布助手。请仔细观察提供的商品图片，并生成适合在闲鱼发布的商品信息。

要求：
1. title: 吸引人且写实的闲鱼商品标题（30字以内），包含品牌、品类和核心卖点，不虚构或夸大成色。
2. description: 详细的商品描述（200-500字），包括外观成色、规格特征、使用感受、转手原因说明及交易提示，语气真诚生活化。
3. category: 精确的商品分类名称（如：数码产品、手机数码、居家日用、服饰鞋包、运动户外等）。

请直接以标准 JSON 格式输出，不要包含任何 markdown 代码块标记，格式如下：
{"title": "...", "description": "...", "category": "..."}"""


class AiFailoverError(Exception):
    """AI 容灾服务基类异常"""
    pass


class NoProviderConfiguredError(AiFailoverError):
    """未配置或未启用任何 AI 服务商"""
    pass


class AllProvidersFailedError(AiFailoverError):
    """所有已配置的 AI 服务商均调用失败"""

    def __init__(self, message: str, errors: List[str]) -> None:
        super().__init__(message)
        self.message = message
        self.errors = errors


def _parse_listing_json(raw_text: str) -> dict[str, str]:
    """从模型返回文本中解析闲鱼文案 JSON 对象"""
    text = raw_text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if len(lines) >= 2 and lines[0].startswith("```"):
            if lines[-1].strip() == "```":
                lines = lines[1:-1]
            else:
                lines = lines[1:]
        text = "\n".join(lines).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]

    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("返回内容解析后不是有效的 JSON 字典")

    title = str(data.get("title") or "").strip()
    description = str(data.get("description") or "").strip()
    category = str(data.get("category") or "其他").strip()

    if not title:
        raise ValueError("返回 JSON 中缺少 title 字段")
    if not description:
        raise ValueError("返回 JSON 中缺少 description 字段")

    return {
        "title": title,
        "description": description,
        "category": category,
    }


class AiProviderFailoverService:
    """AI 服务商容灾调用服务类"""

    def __init__(
        self,
        session: AsyncSession,
        fernet_key: str | bytes = "",
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.session = session
        self._fernet_key = fernet_key
        self._http_client = http_client

    def _get_encryption_key(self) -> str:
        """获取对称加密密钥"""
        if self._fernet_key:
            return self._fernet_key if isinstance(self._fernet_key, str) else self._fernet_key.decode("utf-8")
        from app.core.config import get_settings
        key = get_settings().ai_provider_encryption_key
        if not key or not key.strip():
            raise ValueError("未配置 AI 服务商加密密钥（AI_PROVIDER_ENCRYPTION_KEY）")
        return key.strip()

    async def generate_listing(
        self,
        image_bytes: bytes,
        hint: Optional[str] = None,
    ) -> dict[str, Any]:
        """根据商品图片生成闲鱼发布文案（容灾轮询）

        Args:
            image_bytes: 图片二进制数据。
            hint: 可选的补充要求或关键词。

        Returns:
            dict: {title, description, category, provider_used}

        Raises:
            NoProviderConfiguredError: 未配置或未启用任何服务商（对应 503）。
            AllProvidersFailedError: 所有服务商均调用失败（对应 502）。
        """
        if not image_bytes:
            raise ValueError("商品图片不能为空")

        # 1. 查询所有已启用的服务商，按 priority 升序、id 升序排列
        stmt = (
            select(AiProviderConfig)
            .where(AiProviderConfig.is_enabled == True)
            .order_by(AiProviderConfig.priority.asc(), AiProviderConfig.id.asc())
        )
        result = await self.session.execute(stmt)
        providers = list(result.scalars().all())

        if not providers:
            raise NoProviderConfiguredError("未配置 AI 服务商，请先在设置中添加")

        # 2. 组装提示词与图片数据
        prompt_text = LISTING_PROMPT
        if hint and hint.strip():
            prompt_text += f"\n\n补充关键词与要求：{hint.strip()}"

        base64_img = base64.b64encode(image_bytes).decode("utf-8")
        provider_errors: List[str] = []

        # 3. 逐条轮询尝试
        for provider in providers:
            plain_key = ""
            try:
                plain_key = decrypt_api_key(provider.api_key, self._get_encryption_key())
            except Exception as exc:
                err_msg = f"解密 API Key 失败: {str(exc)}"
                provider.last_error = f"[{provider.name}] {err_msg}"[:1000]
                await self.session.commit()
                provider_errors.append(f"[{provider.name}] {err_msg}")
                continue

            url = build_openai_url(provider.base_url, "chat/completions")
            payload = {
                "model": provider.model,
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt_text},
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{base64_img}"
                                },
                            },
                        ],
                    }
                ],
                "temperature": 0.7,
                "max_tokens": 1024,
            }
            headers = {
                "Authorization": f"Bearer {plain_key}",
                "Content-Type": "application/json",
            }

            timeout_sec = float(provider.timeout_seconds or 30)

            try:
                if self._http_client is not None:
                    resp = await self._http_client.post(url, json=payload, headers=headers, timeout=timeout_sec)
                else:
                    async with httpx.AsyncClient(timeout=timeout_sec) as client:
                        resp = await client.post(url, json=payload, headers=headers)

                if not resp.is_success:
                    err_summary = extract_response_error(resp)
                    if plain_key and plain_key in err_summary:
                        err_summary = err_summary.replace(plain_key, "****")
                    err_msg = f"HTTP {resp.status_code}: {err_summary}"
                    provider.last_error = f"[{provider.name}] {err_msg}"[:1000]
                    await self.session.commit()
                    provider_errors.append(f"[{provider.name}] {err_msg}")
                    continue

                resp_data = resp.json()
                choices = resp_data.get("choices")
                if not choices or not isinstance(choices, list):
                    err_msg = "返回数据中缺少 choices 字段"
                    provider.last_error = f"[{provider.name}] {err_msg}"[:1000]
                    await self.session.commit()
                    provider_errors.append(f"[{provider.name}] {err_msg}")
                    continue

                raw_content = choices[0].get("message", {}).get("content", "")
                if not raw_content or not isinstance(raw_content, str):
                    err_msg = "返回消息正文为空"
                    provider.last_error = f"[{provider.name}] {err_msg}"[:1000]
                    await self.session.commit()
                    provider_errors.append(f"[{provider.name}] {err_msg}")
                    continue

                listing = _parse_listing_json(raw_content)

                # 成功！更新状态并清除历史错误
                provider.last_error = None
                provider.last_used_at = datetime.now()
                await self.session.commit()

                return {
                    "title": listing["title"],
                    "description": listing["description"],
                    "category": listing["category"],
                    "provider_used": provider.name,
                }

            except httpx.TimeoutException:
                err_msg = f"请求超时（{int(timeout_sec)}秒）"
                provider.last_error = f"[{provider.name}] {err_msg}"[:1000]
                await self.session.commit()
                provider_errors.append(f"[{provider.name}] {err_msg}")
                continue
            except Exception as exc:
                err_msg = f"调用异常: {type(exc).__name__}: {str(exc)}"
                if plain_key and plain_key in err_msg:
                    err_msg = err_msg.replace(plain_key, "****")
                provider.last_error = f"[{provider.name}] {err_msg}"[:1000]
                await self.session.commit()
                provider_errors.append(f"[{provider.name}] {err_msg}")
                continue

        # 全部服务商均失败
        raise AllProvidersFailedError(
            message="所有 AI 服务商均调用失败",
            errors=provider_errors,
        )
