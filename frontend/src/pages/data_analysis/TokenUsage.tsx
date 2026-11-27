/**
 * Token 用量看板
 *
 * 按用户、按天汇总过去 N 天的 AI token 用量。
 * 后端接口 GET /api/v1/stats/token-usage，权限由后端控制：
 * 普通用户只能看自己的用量，ADMIN 可以看全部用户。
 */
import { Fragment, useEffect, useState, useCallback } from 'react'
import {
  Coins,
  RefreshCw,
  ChevronDown,
  ChevronRight,
  PhoneIncoming,
  PhoneOutgoing,
  Hash,
  User,
} from 'lucide-react'
import {
  getTokenUsage,
  type TokenUsageByUser,
  type TokenUsageData,
} from '@/api/tokenUsage'
import { useUIStore } from '@/store/uiStore'
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  ResponsiveContainer,
  Legend,
} from 'recharts'

/** 天数选项 */
const DAYS_OPTIONS = [
  { value: 7, label: '近7天' },
  { value: 14, label: '近14天' },
  { value: 30, label: '近30天' },
] as const

export function TokenUsage() {
  const { addToast } = useUIStore()
  const [days, setDays] = useState<number>(7)
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState<TokenUsageData | null>(null)
  /** 展开了每日明细的用户 id 集合 */
  const [expandedUsers, setExpandedUsers] = useState<Set<number>>(new Set())

  /** 获取数据 */
  const fetchData = useCallback(async () => {
    setLoading(true)
    try {
      const result = await getTokenUsage(days)
      if (result.success && result.data) {
        setData(result.data)
        // 默认展开第一个用户（只有一个用户时也展开，方便直接看到趋势）
        if (result.data.by_user.length > 0) {
          setExpandedUsers(new Set([result.data.by_user[0].user_id]))
        } else {
          setExpandedUsers(new Set())
        }
      } else {
        addToast({ type: 'error', message: result.message || '获取 Token 用量失败' })
        setData(null)
      }
    } catch {
      addToast({ type: 'error', message: '获取 Token 用量失败，请稍后重试' })
      setData(null)
    } finally {
      setLoading(false)
    }
  }, [days, addToast])

  /** 天数变化时重新获取数据 */
  useEffect(() => {
    fetchData()
  }, [fetchData])

  /** 切换某个用户的展开状态 */
  const toggleUser = (userId: number) => {
    setExpandedUsers((prev) => {
      const next = new Set(prev)
      if (next.has(userId)) {
        next.delete(userId)
      } else {
        next.add(userId)
      }
      return next
    })
  }

  /** 格式化日期（2026-11-27 -> 11/27） */
  const formatDate = (dateStr: string): string => {
    const parts = dateStr.split('-')
    if (parts.length !== 3) return dateStr
    return `${parts[1]}/${parts[2]}`
  }

  /** 数字千分位 */
  const formatNumber = (num: number): string => {
    return num.toLocaleString('en-US')
  }

  const totals = data?.totals
  const users: TokenUsageByUser[] = data?.by_user ?? []

  return (
    <div className="space-y-4">
      {/* 顶部操作栏 */}
      <div className="flex flex-col sm:flex-row items-start sm:items-center justify-between gap-3 bg-white dark:bg-slate-800 rounded-lg p-4 shadow-sm">
        <div>
          <h2 className="text-lg font-semibold text-gray-800 dark:text-gray-200">
            Token 用量
          </h2>
          {data && (
            <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
              {data.start_date} 至 {data.end_date}（共 {data.days} 天）
            </p>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {/* 天数选择 */}
          <div className="flex rounded-md overflow-hidden border border-gray-300 dark:border-gray-600">
            {DAYS_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                className={`px-3 py-1.5 text-sm transition-colors ${
                  days === opt.value
                    ? 'bg-blue-500 text-white'
                    : 'bg-white dark:bg-slate-700 text-gray-700 dark:text-gray-200 hover:bg-gray-100 dark:hover:bg-slate-600'
                }`}
                onClick={() => setDays(opt.value)}
              >
                {opt.label}
              </button>
            ))}
          </div>

          {/* 刷新按钮 */}
          <button
            className="p-1.5 rounded-md border border-gray-300 dark:border-gray-600 bg-white dark:bg-slate-700 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-slate-600 transition-colors disabled:opacity-50"
            onClick={fetchData}
            disabled={loading}
            title="刷新"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {/* 合计指标卡片 */}
      <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <div className="bg-white dark:bg-slate-800 rounded-lg p-4 shadow-sm">
          <div className="flex items-center gap-2 text-gray-500 dark:text-gray-400 text-sm">
            <Coins className="w-4 h-4" />
            总 Token 数
          </div>
          <div className="text-2xl font-bold text-gray-800 dark:text-gray-100 mt-1">
            {loading ? '--' : formatNumber(totals?.total_tokens ?? 0)}
          </div>
        </div>
        <div className="bg-white dark:bg-slate-800 rounded-lg p-4 shadow-sm">
          <div className="flex items-center gap-2 text-gray-500 dark:text-gray-400 text-sm">
            <PhoneIncoming className="w-4 h-4" />
            Prompt Token
          </div>
          <div className="text-2xl font-bold text-gray-800 dark:text-gray-100 mt-1">
            {loading ? '--' : formatNumber(totals?.prompt_tokens ?? 0)}
          </div>
        </div>
        <div className="bg-white dark:bg-slate-800 rounded-lg p-4 shadow-sm">
          <div className="flex items-center gap-2 text-gray-500 dark:text-gray-400 text-sm">
            <PhoneOutgoing className="w-4 h-4" />
            Completion Token
          </div>
          <div className="text-2xl font-bold text-gray-800 dark:text-gray-100 mt-1">
            {loading ? '--' : formatNumber(totals?.completion_tokens ?? 0)}
          </div>
        </div>
        <div className="bg-white dark:bg-slate-800 rounded-lg p-4 shadow-sm">
          <div className="flex items-center gap-2 text-gray-500 dark:text-gray-400 text-sm">
            <Hash className="w-4 h-4" />
            调用次数
          </div>
          <div className="text-2xl font-bold text-gray-800 dark:text-gray-100 mt-1">
            {loading ? '--' : formatNumber(totals?.calls ?? 0)}
          </div>
        </div>
      </div>

      {/* 按用户汇总 */}
      <div className="bg-white dark:bg-slate-800 rounded-lg p-4 shadow-sm">
        <h3 className="text-base font-semibold text-gray-800 dark:text-gray-200 mb-3">
          按用户汇总
        </h3>
        {loading ? (
          <p className="text-sm text-gray-500 dark:text-gray-400 py-6 text-center">加载中…</p>
        ) : users.length === 0 ? (
          <p className="text-sm text-gray-500 dark:text-gray-400 py-6 text-center">
            暂无用量记录。AI 调用产生用量后，这里会按用户汇总显示。
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-gray-500 dark:text-gray-400 border-b border-gray-200 dark:border-gray-700">
                  <th className="py-2 pr-2 font-medium w-8"></th>
                  <th className="py-2 pr-2 font-medium">用户</th>
                  <th className="py-2 pr-2 font-medium text-right">总 Token</th>
                  <th className="py-2 pr-2 font-medium text-right">Prompt</th>
                  <th className="py-2 pr-2 font-medium text-right">Completion</th>
                  <th className="py-2 font-medium text-right">调用次数</th>
                </tr>
              </thead>
              <tbody>
                {users.map((user) => {
                  const expanded = expandedUsers.has(user.user_id)
                  return (
                    <Fragment key={user.user_id}>
                      <tr
                        className="border-b border-gray-100 dark:border-gray-700/50 hover:bg-gray-50 dark:hover:bg-slate-700/50 cursor-pointer"
                        onClick={() => toggleUser(user.user_id)}
                      >
                        <td className="py-2.5 pr-2 text-gray-400">
                          {expanded ? (
                            <ChevronDown className="w-4 h-4" />
                          ) : (
                            <ChevronRight className="w-4 h-4" />
                          )}
                        </td>
                        <td className="py-2.5 pr-2 font-medium text-gray-800 dark:text-gray-200">
                          <span className="inline-flex items-center gap-1.5">
                            <User className="w-4 h-4 text-gray-400" />
                            {user.username}
                          </span>
                        </td>
                        <td className="py-2.5 pr-2 text-right font-semibold text-gray-800 dark:text-gray-100">
                          {formatNumber(user.total_tokens)}
                        </td>
                        <td className="py-2.5 pr-2 text-right text-gray-600 dark:text-gray-300">
                          {formatNumber(user.prompt_tokens)}
                        </td>
                        <td className="py-2.5 pr-2 text-right text-gray-600 dark:text-gray-300">
                          {formatNumber(user.completion_tokens)}
                        </td>
                        <td className="py-2.5 text-right text-gray-600 dark:text-gray-300">
                          {formatNumber(user.calls)}
                        </td>
                      </tr>
                      {expanded && (
                        <tr>
                          <td colSpan={6} className="py-3 px-1 bg-gray-50 dark:bg-slate-700/30">
                            {/* 每日趋势图 */}
                            <div className="h-56 mb-3">
                              <ResponsiveContainer width="100%" height="100%">
                                <LineChart
                                  data={user.by_day}
                                  margin={{ top: 5, right: 12, left: 0, bottom: 0 }}
                                >
                                  <CartesianGrid strokeDasharray="3 3" stroke="#e5e7eb" />
                                  <XAxis
                                    dataKey="date"
                                    tickFormatter={formatDate}
                                    tick={{ fontSize: 12 }}
                                  />
                                  <YAxis tick={{ fontSize: 12 }} />
                                  <Tooltip
                                    labelFormatter={(label) => String(label ?? '')}
                                    formatter={(value, name) => [
                                      formatNumber(Number(value ?? 0)),
                                      name === 'total_tokens' ? '总 Token' : String(name),
                                    ]}
                                  />
                                  <Legend />
                                  <Line
                                    type="monotone"
                                    dataKey="total_tokens"
                                    name="总 Token"
                                    stroke="#3b82f6"
                                    strokeWidth={2}
                                    dot={{ r: 3 }}
                                  />
                                  <Line
                                    type="monotone"
                                    dataKey="prompt_tokens"
                                    name="Prompt"
                                    stroke="#22c55e"
                                    strokeWidth={1.5}
                                    dot={false}
                                  />
                                  <Line
                                    type="monotone"
                                    dataKey="completion_tokens"
                                    name="Completion"
                                    stroke="#f59e0b"
                                    strokeWidth={1.5}
                                    dot={false}
                                  />
                                </LineChart>
                              </ResponsiveContainer>
                            </div>
                            {/* 每日明细表 */}
                            <table className="w-full text-sm">
                              <thead>
                                <tr className="text-left text-gray-500 dark:text-gray-400 border-b border-gray-200 dark:border-gray-700">
                                  <th className="py-1.5 pr-2 font-medium">日期</th>
                                  <th className="py-1.5 pr-2 font-medium text-right">Prompt</th>
                                  <th className="py-1.5 pr-2 font-medium text-right">Completion</th>
                                  <th className="py-1.5 pr-2 font-medium text-right">总 Token</th>
                                  <th className="py-1.5 font-medium text-right">调用次数</th>
                                </tr>
                              </thead>
                              <tbody>
                                {user.by_day.map((day) => (
                                  <tr
                                    key={day.date}
                                    className="border-b border-gray-100 dark:border-gray-700/50 last:border-0"
                                  >
                                    <td className="py-1.5 pr-2 text-gray-700 dark:text-gray-300">
                                      {day.date}
                                    </td>
                                    <td className="py-1.5 pr-2 text-right text-gray-600 dark:text-gray-300">
                                      {formatNumber(day.prompt_tokens)}
                                    </td>
                                    <td className="py-1.5 pr-2 text-right text-gray-600 dark:text-gray-300">
                                      {formatNumber(day.completion_tokens)}
                                    </td>
                                    <td className="py-1.5 pr-2 text-right font-medium text-gray-800 dark:text-gray-100">
                                      {formatNumber(day.total_tokens)}
                                    </td>
                                    <td className="py-1.5 text-right text-gray-600 dark:text-gray-300">
                                      {formatNumber(day.calls)}
                                    </td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
