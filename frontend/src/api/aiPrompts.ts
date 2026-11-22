/**
 * AI 提示词配置接口
 *
 * 功能：
 * 1. 获取两条 AI 提示词（发布文案生成 / 挑同款定价）
 * 2. 更新提示词（只更新传了的字段）
 * 3. 恢复默认提示词
 *
 * 提示词存在后端全局键值表 xy_system_settings（ai.prompts.listing / ai.prompts.pick），
 * 保存后下一次 AI 调用即生效；页面上绝不展示任何 API Key。
 */
import { get, post, put } from '@/utils/request'

const PREFIX = '/api/v1/ai'

// ==================== 类型定义 ====================

/** 两条 AI 提示词及状态 */
export interface AiPrompts {
  listing_prompt: string
  pick_prompt: string
  listing_is_default: boolean
  pick_is_default: boolean
  updated_at: string | null
}

/** 更新 AI 提示词请求参数（只更新传了的字段） */
export interface UpdateAiPromptsParams {
  listing_prompt?: string
  pick_prompt?: string
}

// ==================== 接口方法 ====================

/** 获取两条 AI 提示词（未配置过时返回默认提示词 + is_default true） */
export const getAiPrompts = (): Promise<AiPrompts> =>
  get<AiPrompts>(`${PREFIX}/prompts`)

/** 更新 AI 提示词（只更新传了的字段；校验不过后端返回 400 明确错误） */
export const updateAiPrompts = (params: UpdateAiPromptsParams): Promise<AiPrompts> =>
  put<AiPrompts>(`${PREFIX}/prompts`, params)

/** 恢复默认提示词（删除两条自定义配置） */
export const resetAiPrompts = (): Promise<AiPrompts> =>
  post<AiPrompts>(`${PREFIX}/prompts/reset`, {})
