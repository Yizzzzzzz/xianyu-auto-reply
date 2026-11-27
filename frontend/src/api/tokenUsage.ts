/**
 * Token 用量统计 API
 *
 * 调用后端 GET /api/v1/stats/token-usage，按用户、按天汇总 AI token 用量。
 * 权限由后端控制：普通用户只能看自己的用量，ADMIN 可以看全部用户。
 */
import { get } from '@/utils/request'

const TOKEN_USAGE_PREFIX = '/api/v1/stats'

/** 每日用量 */
export interface TokenUsageDayItem {
  date: string
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  calls: number
}

/** 单个用户的用量汇总 */
export interface TokenUsageByUser {
  user_id: number
  username: string
  total_tokens: number
  prompt_tokens: number
  completion_tokens: number
  calls: number
  by_day: TokenUsageDayItem[]
}

/** 全量合计 */
export interface TokenUsageTotals {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
  calls: number
}

/** 看板响应数据 */
export interface TokenUsageData {
  days: number
  start_date: string
  end_date: string
  by_user: TokenUsageByUser[]
  totals: TokenUsageTotals
}

/** API 响应格式 */
export interface TokenUsageResponse {
  success: boolean
  message: string | null
  data: TokenUsageData | null
}

/**
 * 获取过去 N 天的 token 用量
 * @param days 过去几天，默认 7，范围 1-90
 */
export const getTokenUsage = async (days = 7): Promise<TokenUsageResponse> => {
  return get<TokenUsageResponse>(`${TOKEN_USAGE_PREFIX}/token-usage`, {
    params: { days },
  })
}
