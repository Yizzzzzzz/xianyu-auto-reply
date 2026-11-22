/**
 * AI 服务商管理页面
 *
 * 功能：
 * 1. AI 服务商配置列表展示与空状态
 * 2. 启用状态快速切换
 * 3. 优先级上移/下移重新排序
 * 4. 连通性测试
 * 5. 新增、编辑与删除（删除二次确认）
 */
import { useCallback, useEffect, useState } from 'react'
import { ArrowDown, ArrowUp, Bot, Loader2, Plus } from 'lucide-react'
import {
  deleteAiProvider,
  getAiProviders,
  reorderAiProviders,
  testAiProvider,
  updateAiProvider,
  type AiProvider,
} from '@/api/aiProviders'
import { ConfirmModal } from '@/components/common/ConfirmModal'
import { Loading } from '@/components/common/Loading'
import { useUIStore } from '@/store/uiStore'
import { getApiErrorMessage } from '@/utils/apiError'
import { AIProviderModal } from './AIProviderModal'

const formatDateTime = (value?: string | null): string => {
  if (!value) return '-'
  return value.replace('T', ' ').slice(0, 19)
}

export function AIProviders() {
  const { addToast } = useUIStore()
  const [providers, setProviders] = useState<AiProvider[]>([])
  const [loading, setLoading] = useState(true)
  const [showCreateModal, setShowCreateModal] = useState(false)
  const [editingProvider, setEditingProvider] = useState<AiProvider | null>(null)
  const [deleteTarget, setDeleteTarget] = useState<AiProvider | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [togglingId, setTogglingId] = useState<number | null>(null)
  const [testingId, setTestingId] = useState<number | null>(null)
  const [reordering, setReordering] = useState(false)

  const loadProviders = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getAiProviders()
      setProviders(Array.isArray(data) ? data : [])
    } catch (error) {
      setProviders([])
      addToast({ type: 'error', message: getApiErrorMessage(error, '获取 AI 服务商列表失败') })
    } finally {
      setLoading(false)
    }
  }, [addToast])

  useEffect(() => {
    void loadProviders()
  }, [loadProviders])

  const handleToggleEnabled = async (provider: AiProvider) => {
    setTogglingId(provider.id)
    const nextEnabled = !provider.is_enabled
    try {
      const updated = await updateAiProvider(provider.id, { is_enabled: nextEnabled })
      setProviders((current) =>
        current.map((item) => (item.id === provider.id ? { ...item, is_enabled: updated.is_enabled } : item)),
      )
      addToast({
        type: 'success',
        message: `${provider.name} 已${nextEnabled ? '启用' : '停用'}`,
      })
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '更新状态失败') })
    } finally {
      setTogglingId(null)
    }
  }

  const handleMove = async (index: number, direction: 'up' | 'down') => {
    const targetIndex = direction === 'up' ? index - 1 : index + 1
    if (targetIndex < 0 || targetIndex >= providers.length) return
    const nextList = [...providers]
    const [removed] = nextList.splice(index, 1)
    nextList.splice(targetIndex, 0, removed)

    setReordering(true)
    try {
      const ids = nextList.map((item) => item.id)
      const result = await reorderAiProviders(ids)
      setProviders(nextList)
      addToast({ type: 'success', message: result.message || '服务商调用顺序已更新' })
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '更新排序失败') })
      void loadProviders()
    } finally {
      setReordering(false)
    }
  }

  const handleTest = async (provider: AiProvider) => {
    setTestingId(provider.id)
    try {
      const result = await testAiProvider(provider.id)
      if (result.ok) {
        addToast({
          type: 'success',
          message: result.message || '服务商连接测试成功',
        })
      } else {
        addToast({
          type: 'error',
          message: result.message || `服务商连接失败 (${result.status})`,
        })
      }
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '服务商连接测试异常') })
    } finally {
      setTestingId(null)
    }
  }

  const handleDelete = async () => {
    if (!deleteTarget) return
    setDeleting(true)
    try {
      const result = await deleteAiProvider(deleteTarget.id)
      addToast({ type: 'success', message: result.message || '服务商已删除' })
      setDeleteTarget(null)
      await loadProviders()
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '删除服务商失败') })
    } finally {
      setDeleting(false)
    }
  }

  return (
    <div className="flex min-h-0 flex-col space-y-3 sm:space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="page-title">AI 服务商</h1>
          <p className="page-description">
            填写服务商的 Base URL、API Key 和模型名；可配置多个，商品发布时按优先级依次尝试，挂了自动换下一个；Key 加密存储，页面只显示掩码。
          </p>
        </div>
        <button
          type="button"
          className="btn-ios-primary"
          onClick={() => setShowCreateModal(true)}
        >
          <Plus className="h-4 w-4" />新建服务商
        </button>
      </div>

      <div className="vben-card relative flex min-h-[440px] flex-col overflow-hidden sm:min-h-[520px] lg:h-[calc(100vh-210px)] lg:min-h-[440px]">
        {loading && (
          <div className="absolute inset-0 z-20 flex items-center justify-center bg-white/80 backdrop-blur-sm dark:bg-slate-900/80">
            <Loading text="正在加载 AI 服务商..." />
          </div>
        )}

        <div className="table-scroll flex-1">
          <table className="table-ios min-w-[1000px]">
            <thead>
              <tr>
                <th className="w-24">排序</th>
                <th>服务商名称</th>
                <th>Base URL</th>
                <th>模型名</th>
                <th className="w-24 text-center">启用</th>
                <th>上次使用时间</th>
                <th>上次错误</th>
                <th className="w-56 text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {!loading && providers.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-20 text-center">
                    <Bot className="mx-auto mb-3 h-12 w-12 text-slate-300 dark:text-slate-600" />
                    <p className="text-sm text-slate-500 dark:text-slate-400">
                      暂无 AI 服务商配置，请点击右上角新建
                    </p>
                  </td>
                </tr>
              ) : (
                providers.map((provider, index) => (
                  <tr key={provider.id}>
                    <td>
                      <div className="flex items-center gap-1">
                        <span className="w-4 text-center text-xs font-mono text-slate-400">
                          {index + 1}
                        </span>
                        <button
                          type="button"
                          disabled={index === 0 || reordering}
                          onClick={() => void handleMove(index, 'up')}
                          className="rounded p-1 text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-30"
                          title="上移"
                          aria-label="上移"
                        >
                          <ArrowUp className="h-3.5 w-3.5" />
                        </button>
                        <button
                          type="button"
                          disabled={index === providers.length - 1 || reordering}
                          onClick={() => void handleMove(index, 'down')}
                          className="rounded p-1 text-slate-500 hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-30"
                          title="下移"
                          aria-label="下移"
                        >
                          <ArrowDown className="h-3.5 w-3.5" />
                        </button>
                      </div>
                    </td>
                    <td className="max-w-44 font-medium text-slate-900 dark:text-slate-100 truncate">
                      {provider.name}
                    </td>
                    <td className="max-w-64 font-mono text-xs text-slate-600 dark:text-slate-300 truncate" title={provider.base_url}>
                      {provider.base_url}
                    </td>
                    <td className="max-w-40 font-mono text-xs text-slate-700 dark:text-slate-200 truncate" title={provider.model}>
                      {provider.model}
                    </td>
                    <td className="text-center">
                      <label className="switch-ios">
                        <input
                          type="checkbox"
                          checked={provider.is_enabled}
                          disabled={togglingId === provider.id}
                          onChange={() => void handleToggleEnabled(provider)}
                        />
                        <span className="switch-slider" />
                      </label>
                    </td>
                    <td className="whitespace-nowrap text-xs text-slate-500 dark:text-slate-400">
                      {formatDateTime(provider.last_used_at)}
                    </td>
                    <td className="max-w-60 truncate">
                      {provider.last_error ? (
                        <span className="text-xs text-red-500 font-mono" title={provider.last_error}>
                          {provider.last_error}
                        </span>
                      ) : null}
                    </td>
                    <td className="text-right whitespace-nowrap">
                      <div className="flex items-center justify-end gap-x-3">
                        <button
                          type="button"
                          disabled={testingId === provider.id}
                          className="btn-ios-link text-sm inline-flex items-center gap-1"
                          onClick={() => void handleTest(provider)}
                        >
                          {testingId === provider.id && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                          测试连接
                        </button>
                        <button
                          type="button"
                          className="btn-ios-link text-sm"
                          onClick={() => setEditingProvider(provider)}
                        >
                          编辑
                        </button>
                        <button
                          type="button"
                          className="btn-ios-link text-sm text-red-600 hover:text-red-700 dark:text-red-400"
                          onClick={() => setDeleteTarget(provider)}
                        >
                          删除
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {showCreateModal && (
        <AIProviderModal
          onClose={() => setShowCreateModal(false)}
          onSaved={() => void loadProviders()}
        />
      )}

      {editingProvider && (
        <AIProviderModal
          provider={editingProvider}
          onClose={() => setEditingProvider(null)}
          onSaved={() => void loadProviders()}
        />
      )}

      {deleteTarget && (
        <ConfirmModal
          isOpen={Boolean(deleteTarget)}
          title="删除服务商"
          message={
            <span>
              确定要删除 AI 服务商 <strong>{deleteTarget.name}</strong> 吗？删除后不可恢复。
            </span>
          }
          type="danger"
          confirmText="确定删除"
          cancelText="取消"
          loading={deleting}
          onConfirm={() => void handleDelete()}
          onCancel={() => setDeleteTarget(null)}
        />
      )}
    </div>
  )
}

export default AIProviders
