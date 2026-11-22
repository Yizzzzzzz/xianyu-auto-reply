/**
 * AI 服务商配置接口
 *
 * 功能：
 * 1. AI 服务商的增删改查
 * 2. 优先级重新排序
 * 3. 连通性测试
 */
import { del, get, post, put } from '@/utils/request'

const PREFIX = '/api/v1/ai'

// ==================== 类型定义 ====================

/** AI 服务商配置（API Key 为脱敏掩码，绝不返回明文） */
export interface AiProvider {
  id: number
  name: string
  base_url: string
  api_key: string
  model: string
  is_enabled: boolean
  priority: number
  timeout_seconds: number
  last_error: string | null
  last_used_at: string | null
  created_at: string | null
  updated_at: string | null
}

/** 新建 AI 服务商请求参数 */
export interface CreateAiProviderParams {
  name: string
  base_url: string
  api_key: string
  model: string
  is_enabled?: boolean
  priority?: number
  timeout_seconds?: number
}

/** 更新 AI 服务商请求参数（api_key 为空或为掩码时保持原 Key 不变） */
export interface UpdateAiProviderParams {
  name?: string
  base_url?: string
  api_key?: string
  model?: string
  is_enabled?: boolean
  priority?: number
  timeout_seconds?: number
}

/** 服务商连通性测试响应 */
export interface AiProviderTestResult {
  ok: boolean
  status: number
  message: string
}

/** 通用操作响应 */
export interface SimpleActionResponse {
  ok: boolean
  message: string
}

// ==================== 接口方法 ====================

/** 获取所有 AI 服务商列表（按优先级和 ID 升序，裸数组） */
export const getAiProviders = (): Promise<AiProvider[]> =>
  get<AiProvider[]>(`${PREFIX}/providers`)

/** 新建 AI 服务商配置 */
export const createAiProvider = (params: CreateAiProviderParams): Promise<AiProvider> =>
  post<AiProvider>(`${PREFIX}/providers`, params)

/** 更新 AI 服务商配置 */
export const updateAiProvider = (id: number, params: UpdateAiProviderParams): Promise<AiProvider> =>
  put<AiProvider>(`${PREFIX}/providers/${id}`, params)

/** 删除 AI 服务商配置（硬删除） */
export const deleteAiProvider = (id: number): Promise<SimpleActionResponse> =>
  del<SimpleActionResponse>(`${PREFIX}/providers/${id}`)

/** 批量更新 AI 服务商调用顺序 */
export const reorderAiProviders = (ids: number[]): Promise<SimpleActionResponse> =>
  post<SimpleActionResponse>(`${PREFIX}/providers/reorder`, { ids })

/** 测试指定 AI 服务商接口连通性 */
export const testAiProvider = (id: number): Promise<AiProviderTestResult> =>
  post<AiProviderTestResult>(`${PREFIX}/providers/${id}/test`, {})
