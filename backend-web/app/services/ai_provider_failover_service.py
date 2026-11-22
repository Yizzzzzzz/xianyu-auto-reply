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
from common.models.system_setting import SystemSetting
from common.services.ai_provider_service import build_openai_url, extract_response_error

# AI 提示词在全局键值表 xy_system_settings 中的键；可通过后端接口 /api/v1/ai/prompts 修改，
# 保存后下一次调用即生效；无自定义值（未配置或为空）时回退到代码里的默认常量。
LISTING_PROMPT_KEY = "ai.prompts.listing"
PICK_PROMPT_KEY = "ai.prompts.pick"
IDENTIFY_PROMPT_KEY = "ai.prompts.identify"

# 固定提示词：后端定义，无需前端传入
LISTING_PROMPT = """你是一个专业的闲鱼二手交易平台商品发布助手。请仔细观察提供的商品图片，并生成适合在闲鱼发布的商品信息。

要求：
1. title: 吸引人且写实的闲鱼商品标题（30字以内），包含品牌、品类和核心卖点，不虚构或夸大成色。
2. description: 详细的商品描述（200-500字），包括外观成色、规格特征、使用感受、转手原因说明及交易提示，语气真诚生活化。
3. category: 精确的商品分类名称（如：数码产品、手机数码、居家日用、服饰鞋包、运动户外等）。
4. search_keyword: 用于在闲鱼搜同款比价的简短核心商品词（2-6 字，如“机械键盘”“三层收纳盒”），只写商品本身，不要成色、价格或整句标题。
5. 参数提取：仔细看图片里印着、贴着或显示着的参数信息（品牌、型号、规格、容量、尺寸、版本、内存、颜色等）。把真实看到的参数写进标题（如果是核心识别信息、如型号，且标题放得下）和详情里（详情中单独列一段“商品参数”，逐项列出看到的参数）；不要编造图片里没有的参数，不要猜测型号，不要把相似型号当作图中型号。search_keyword 优先用图里的核心参数/型号 + 品类来提炼（例如图里是某型号键盘，就搜“型号+键盘”，而不是只搜“机械键盘”）；图里没有型号时再用通用品类词。

请直接以标准 JSON 格式输出，不要包含任何 markdown 代码块标记，格式如下：
{"title": "...", "description": "...", "category": "...", "search_keyword": "..."}"""



PICK_PROMPT = """你是一个专业的闲鱼二手交易平台定价助手。请仔细观察提供的商品图片，并从下面列出的同款在售商品中，挑出和图片里商品最相关的一条（同一款商品/型号、成色最接近；不要只看标题相似，要结合图片里的品牌、型号、外观判断）。

候选同款（编号从 0 开始）：
{candidates}

要求：
1. 只输出标准 JSON，不要 markdown 代码块标记，格式如下：
{"index": 0, "suggested_price": 123.45, "reason": "..."}
2. index 必须是上面列表里真实存在的编号。
3. suggested_price 以挑中那条的价格为基准，结合成色差异可小幅调整，必须大于 0；不要凭空编一个和候选价格无关的数字。
4. reason 用一句话说明为什么挑这一条（20 字以内）。
"""


IDENTIFY_PROMPT = """你是一个专业的闲鱼二手交易平台物品识别助手。请仔细观察提供的图片，识别图中的物品并生成结构化介绍。

要求：
1. name: 这是什么物品（写实，包含品牌、品类或核心识别名称）。
2. category: 精确的商品分类名称（如：数码产品、手机数码、居家日用、服饰鞋包、运动户外等）。
3. description: 物品详细介绍（这是什么东西、干嘛用的、怎么用，200-500字，写实不夸大）。图里印着的型号、参数如果看得清就写进介绍，不要编造图里没有的。
4. search_keyword: 用于去闲鱼搜同款比价的简短核心商品词（2-6 字，如“机械键盘”“收纳盒”），只写商品本身，不要成色、价格或修饰词。

请直接以标准 JSON 格式输出，不要包含任何 markdown 代码块标记，格式如下：
{"name": "...", "category": "...", "description": "...", "search_keyword": "..."}"""

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


class ImageGenerationNotSupportedError(AiFailoverError):
    """当前服务商不支持图片生成"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


CHAT_SYSTEM_PROMPT = "你是闲鱼商品识别助手，基于图片和对话回答问题，写实不编造，不知道就说不知道。"


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
    # 旧模型 / 旧版本可能不返回搜索词，缺失时为空串，由调用方回退到标题全文。
    search_keyword = str(data.get("search_keyword") or "").strip()

    if not title:
        raise ValueError("返回 JSON 中缺少 title 字段")
    if not description:
        raise ValueError("返回 JSON 中缺少 description 字段")

    return {
        "title": title,
        "description": description,
        "category": category,
        "search_keyword": search_keyword,
    }



def _parse_pick_json(raw_text: str, count: int) -> dict[str, object]:
    """从模型返回文本中解析“挑最相关同款”JSON 对象"""
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

    index = data.get("index")
    if isinstance(index, bool) or not isinstance(index, int):
        raise ValueError("返回 JSON 中缺少有效的 index 字段")
    if index < 0 or index >= count:
        raise ValueError("返回的 index 超出候选同款范围")

    try:
        suggested_price = float(data.get("suggested_price"))
    except (TypeError, ValueError):
        raise ValueError("返回 JSON 中缺少有效的 suggested_price 字段")
    if suggested_price <= 0:
        raise ValueError("suggested_price 必须大于 0")

    return {
        "index": index,
        "suggested_price": suggested_price,
        "reason": str(data.get("reason") or "").strip(),
    }


def _parse_identify_json(raw_text: str) -> dict[str, str]:
    """从模型返回文本中解析拍照识物 JSON 对象"""
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

    name = str(data.get("name") or "").strip()
    category = str(data.get("category") or "其他").strip()
    description = str(data.get("description") or "").strip()
    search_keyword = str(data.get("search_keyword") or "").strip()

    if not name:
        raise ValueError("返回 JSON 中缺少 name 字段")
    if not description:
        raise ValueError("返回 JSON 中缺少 description 字段")

    return {
        "name": name,
        "category": category,
        "description": description,
        "search_keyword": search_keyword,
    }


def _create_async_client(timeout: float) -> httpx.AsyncClient:
    """创建异步 HTTP 客户端，在代理环境变量格式不兼容时自动降级"""
    try:
        return httpx.AsyncClient(timeout=timeout)
    except Exception:
        return httpx.AsyncClient(timeout=timeout, trust_env=False)


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

    async def _get_prompt(self, key: str, default: str) -> str:
        """读取 xy_system_settings 中的提示词；无自定义值（未配置或为空）时回退到默认常量。

        每次调用都实时读取，不缓存，保存后下一次调用即用新提示词。
        """
        stmt = select(SystemSetting).where(SystemSetting.key == key)
        result = await self.session.execute(stmt)
        record = result.scalars().first()
        if record and record.value and record.value.strip():
            return record.value
        return default

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
            raise NoProviderConfiguredError("未配置 AI 服务商，请先通过后端接口添加服务商")

        # 2. 组装提示词与图片数据（提示词每次实时读配置，未配置则用默认常量）
        prompt_text = await self._get_prompt(LISTING_PROMPT_KEY, LISTING_PROMPT)
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
                "max_tokens": 4096,
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
                    async with _create_async_client(timeout=timeout_sec) as client:
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
                    _fr = choices[0].get("finish_reason") or ""
                    err_msg = "返回消息正文为空" + (f"（finish_reason={_fr}，若为 length 说明输出 token 上限被模型思考过程占满）" if _fr else "")
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
                    "search_keyword": listing["search_keyword"],
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

    async def pick_competitor(
        self,
        image_bytes: bytes,
        competitors: list[dict[str, object]],
    ) -> dict[str, object]:
        """对照商品图片，从候选同款中挑最相关的一条并给出建议价（容灾轮询）

        Args:
            image_bytes: 商品主图二进制数据。
            competitors: 候选同款，每项至少含 title（str）与 price（数字）。

        Returns:
            dict: {competitor_index, suggested_price, reason, provider_used}
        """
        if not image_bytes:
            raise ValueError("商品图片不能为空")
        if not competitors:
            raise ValueError("候选同款不能为空")

        stmt = (
            select(AiProviderConfig)
            .where(AiProviderConfig.is_enabled == True)
            .order_by(AiProviderConfig.priority.asc(), AiProviderConfig.id.asc())
        )
        result = await self.session.execute(stmt)
        providers = list(result.scalars().all())

        if not providers:
            raise NoProviderConfiguredError("未配置 AI 服务商，请先通过后端接口添加服务商")

        candidates = "\n".join(
            f"{i}. 标题：{c.get('title', '')} 价格：¥{c.get('price', '')}"
            for i, c in enumerate(competitors)
        )
        # 提示词每次实时读配置，未配置则用默认常量；{candidates} 由调用处填入候选同款列表
        prompt_template = await self._get_prompt(PICK_PROMPT_KEY, PICK_PROMPT)
        prompt_text = prompt_template.replace("{candidates}", candidates)
        base64_img = base64.b64encode(image_bytes).decode("utf-8")
        provider_errors: List[str] = []

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
                "temperature": 0.3,
                "max_tokens": 2048,
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
                    async with _create_async_client(timeout=timeout_sec) as client:
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
                    _fr = choices[0].get("finish_reason") or ""
                    err_msg = "返回消息正文为空" + (f"（finish_reason={_fr}，若为 length 说明输出 token 上限被模型思考过程占满）" if _fr else "")
                    provider.last_error = f"[{provider.name}] {err_msg}"[:1000]
                    await self.session.commit()
                    provider_errors.append(f"[{provider.name}] {err_msg}")
                    continue

                try:
                    pick = _parse_pick_json(raw_content, len(competitors))
                except Exception as exc:
                    err_msg = f"挑同款结果解析失败: {str(exc)}"
                    provider.last_error = f"[{provider.name}] {err_msg}"[:1000]
                    await self.session.commit()
                    provider_errors.append(f"[{provider.name}] {err_msg}")
                    continue

                provider.last_error = None
                provider.last_used_at = datetime.now()
                await self.session.commit()

                return {
                    "competitor_index": pick["index"],
                    "suggested_price": pick["suggested_price"],
                    "reason": pick["reason"],
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

        raise AllProvidersFailedError(
            message="所有 AI 服务商均调用失败",
            errors=provider_errors,
        )

    async def identify_item(
        self,
        image_bytes: bytes,
        hint: Optional[str] = None,
    ) -> dict[str, Any]:
        """根据商品图片识别物品信息（容灾轮询）

        Args:
            image_bytes: 图片二进制数据。
            hint: 可选的补充要求或关键词。

        Returns:
            dict: {name, category, description, search_keyword, provider_used}

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
            raise NoProviderConfiguredError("未配置 AI 服务商，请先通过后端接口添加服务商")

        # 2. 组装提示词与图片数据（提示词每次实时读配置，未配置则用默认常量）
        prompt_text = await self._get_prompt(IDENTIFY_PROMPT_KEY, IDENTIFY_PROMPT)
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
                "max_tokens": 4096,
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
                    async with _create_async_client(timeout=timeout_sec) as client:
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
                    _fr = choices[0].get("finish_reason") or ""
                    err_msg = "返回消息正文为空" + (f"（finish_reason={_fr}，若为 length 说明输出 token 上限被模型思考过程占满）" if _fr else "")
                    provider.last_error = f"[{provider.name}] {err_msg}"[:1000]
                    await self.session.commit()
                    provider_errors.append(f"[{provider.name}] {err_msg}")
                    continue

                try:
                    identified = _parse_identify_json(raw_content)
                except Exception as exc:
                    err_msg = f"识别结果解析失败: {str(exc)}"
                    provider.last_error = f"[{provider.name}] {err_msg}"[:1000]
                    await self.session.commit()
                    provider_errors.append(f"[{provider.name}] {err_msg}")
                    continue

                # 成功！更新状态并清除历史错误
                provider.last_error = None
                provider.last_used_at = datetime.now()
                await self.session.commit()

                return {
                    "name": identified["name"],
                    "category": identified["category"],
                    "description": identified["description"],
                    "search_keyword": identified["search_keyword"],
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

        raise AllProvidersFailedError(
            message="所有 AI 服务商均调用失败",
            errors=provider_errors,
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """根据历史对话消息进行追问回复（容灾轮询）

        Args:
            messages: 对话历史消息列表，每项必须为 dict 且 role 为 user 或 assistant，content 非空。

        Returns:
            dict: {reply, provider_used}

        Raises:
            ValueError: messages 入参校验不通过。
            NoProviderConfiguredError: 未配置或未启用任何服务商（对应 503）。
            AllProvidersFailedError: 所有服务商均调用失败（对应 502）。
        """
        if not isinstance(messages, list) or not messages:
            raise ValueError("messages 必须是非空列表")

        for idx, item in enumerate(messages):
            if not isinstance(item, dict):
                raise ValueError(f"messages[{idx}] 必须是字典")
            role = item.get("role")
            if role not in ("user", "assistant"):
                raise ValueError(f"messages[{idx}].role 必须为 user 或 assistant")
            content = item.get("content")
            if content is None:
                raise ValueError(f"messages[{idx}].content 不能为空")
            if isinstance(content, str) and not content.strip():
                raise ValueError(f"messages[{idx}].content 不能为空字符串")
            if isinstance(content, list) and len(content) == 0:
                raise ValueError(f"messages[{idx}].content 列表不能为空")
            if not isinstance(content, (str, list)):
                raise ValueError(f"messages[{idx}].content 必须是字符串或列表")

        stmt = (
            select(AiProviderConfig)
            .where(AiProviderConfig.is_enabled == True)
            .order_by(AiProviderConfig.priority.asc(), AiProviderConfig.id.asc())
        )
        result = await self.session.execute(stmt)
        providers = list(result.scalars().all())

        if not providers:
            raise NoProviderConfiguredError("未配置 AI 服务商，请先通过后端接口添加服务商")

        full_messages = [{"role": "system", "content": CHAT_SYSTEM_PROMPT}] + messages
        provider_errors: List[str] = []

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
                "messages": full_messages,
                "temperature": 0.7,
                "max_tokens": 4096,
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
                    async with _create_async_client(timeout=timeout_sec) as client:
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
                    _fr = choices[0].get("finish_reason") or ""
                    err_msg = "返回消息正文为空" + (f"（finish_reason={_fr}，若为 length 说明输出 token 上限被模型思考过程占满）" if _fr else "")
                    provider.last_error = f"[{provider.name}] {err_msg}"[:1000]
                    await self.session.commit()
                    provider_errors.append(f"[{provider.name}] {err_msg}")
                    continue

                provider.last_error = None
                provider.last_used_at = datetime.now()
                await self.session.commit()

                return {
                    "reply": raw_content,
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

        raise AllProvidersFailedError(
            message="所有 AI 服务商均调用失败",
            errors=provider_errors,
        )

    async def generate_image(self, prompt: str) -> dict[str, str]:
        """根据画面描述生成图片（容灾轮询）

        Args:
            prompt: 用户输入的画面描述原文。

        Returns:
            dict: {image_base64, provider_used}

        Raises:
            ValueError: prompt 为空或非字符串。
            NoProviderConfiguredError: 未配置或未启用任何服务商（对应 503）。
            ImageGenerationNotSupportedError: 所有尝试过的服务商均不支持图片生成。
            AllProvidersFailedError: 所有服务商均调用失败（且并非全部为不支持）。
        """
        if not prompt or not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("画面描述 prompt 不能为空")

        stmt = (
            select(AiProviderConfig)
            .where(AiProviderConfig.is_enabled == True)
            .order_by(AiProviderConfig.priority.asc(), AiProviderConfig.id.asc())
        )
        result = await self.session.execute(stmt)
        providers = list(result.scalars().all())

        if not providers:
            raise NoProviderConfiguredError("未配置 AI 服务商，请先通过后端接口添加服务商")

        unsupported_keywords = (
            "unsupported",
            "not support",
            "does not support",
            "不支持",
            "image generation",
            "image",
            "model",
        )
        unsupported_errors: List[str] = []
        provider_errors: List[str] = []

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

            url = build_openai_url(provider.base_url, "images/generations")
            payload = {
                "model": provider.model,
                "prompt": prompt.strip(),
                "n": 1,
                "size": "1024x1024",
                "response_format": "b64_json",
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
                    async with _create_async_client(timeout=timeout_sec) as client:
                        resp = await client.post(url, json=payload, headers=headers)

                if not resp.is_success:
                    err_summary = extract_response_error(resp)
                    if plain_key and plain_key in err_summary:
                        err_summary = err_summary.replace(plain_key, "****")

                    # 判定是否为“不支持”：
                    # 1. 404 或 405 直接算不支持
                    # 2. 400 且错误文本包含关键词算不支持
                    is_unsupported = False
                    if resp.status_code in (404, 405):
                        is_unsupported = True
                    elif resp.status_code == 400:
                        lower_err = err_summary.lower()
                        if any(kw in lower_err for kw in unsupported_keywords):
                            is_unsupported = True

                    err_msg = f"HTTP {resp.status_code}: {err_summary}"
                    provider.last_error = f"[{provider.name}] {err_msg}"[:1000]
                    await self.session.commit()
                    provider_errors.append(f"[{provider.name}] {err_msg}")

                    if is_unsupported:
                        unsupported_errors.append(f"{provider.name} 返回 {resp.status_code} {err_summary}")

                    continue

                resp_data = resp.json()
                data_list = resp_data.get("data")
                if not data_list or not isinstance(data_list, list):
                    err_msg = "返回数据中缺少 data 列表"
                    provider.last_error = f"[{provider.name}] {err_msg}"[:1000]
                    await self.session.commit()
                    provider_errors.append(f"[{provider.name}] {err_msg}")
                    continue

                b64_json = data_list[0].get("b64_json")
                if not b64_json or not isinstance(b64_json, str):
                    err_msg = "返回数据中缺少 b64_json 图片数据"
                    provider.last_error = f"[{provider.name}] {err_msg}"[:1000]
                    await self.session.commit()
                    provider_errors.append(f"[{provider.name}] {err_msg}")
                    continue

                provider.last_error = None
                provider.last_used_at = datetime.now()
                await self.session.commit()

                return {
                    "image_base64": b64_json,
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

        # 循环结束：
        # 如果所有被试过的服务商都返回“不支持”，抛 ImageGenerationNotSupportedError
        # 否则（至少一条是普通失败）抛 AllProvidersFailedError
        if len(unsupported_errors) == len(providers):
            detail_msg = f"当前服务商不支持图片生成：{'；'.join(unsupported_errors)}"
            raise ImageGenerationNotSupportedError(detail_msg)

        raise AllProvidersFailedError(
            message="所有 AI 服务商均调用失败",
            errors=provider_errors,
        )
