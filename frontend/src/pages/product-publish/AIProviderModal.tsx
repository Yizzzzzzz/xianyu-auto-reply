/**
 * AI 服务商新建 / 编辑弹窗
 */
import { useState } from 'react'
import { Loader2, X } from 'lucide-react'
import {
  createAiProvider,
  updateAiProvider,
  type AiProvider,
} from '@/api/aiProviders'
import { PasswordInput } from '@/components/common/PasswordInput'
import { useUIStore } from '@/store/uiStore'
import { getApiErrorMessage } from '@/utils/apiError'

interface AIProviderModalProps {
  provider?: AiProvider | null
  onClose: () => void
  onSaved: () => void
}

export function AIProviderModal({ provider, onClose, onSaved }: AIProviderModalProps) {
  const { addToast } = useUIStore()
  const editing = Boolean(provider)

  const [name, setName] = useState(provider?.name ?? '')
  const [baseUrl, setBaseUrl] = useState(provider?.base_url ?? '')
  const [apiKey, setApiKey] = useState(provider?.api_key ?? '')
  const [model, setModel] = useState(provider?.model ?? '')
  const [isEnabled, setIsEnabled] = useState(provider?.is_enabled ?? true)
  const [timeoutSeconds, setTimeoutSeconds] = useState(
    provider?.timeout_seconds !== undefined ? String(provider.timeout_seconds) : '30',
  )
  const [saving, setSaving] = useState(false)

  const handleSubmit = async (event: React.FormEvent) => {
    event.preventDefault()
    const trimmedName = name.trim()
    const trimmedBaseUrl = baseUrl.trim()
    const trimmedKey = apiKey.trim()
    const trimmedModel = model.trim()
    const parsedTimeout = Number(timeoutSeconds)

    if (!trimmedName) {
      addToast({ type: 'warning', message: '请输入服务商名称' })
      return
    }
    if (!trimmedBaseUrl) {
      addToast({ type: 'warning', message: '请输入 Base URL' })
      return
    }
    if (!editing && !trimmedKey) {
      addToast({ type: 'warning', message: '请输入 API Key' })
      return
    }
    if (!trimmedModel) {
      addToast({ type: 'warning', message: '请输入模型名称' })
      return
    }
    if (!Number.isInteger(parsedTimeout) || parsedTimeout < 1 || parsedTimeout > 300) {
      addToast({ type: 'warning', message: '超时时间必须是 1 到 300 之间的整数秒' })
      return
    }

    setSaving(true)
    try {
      if (editing && provider) {
        await updateAiProvider(provider.id, {
          name: trimmedName,
          base_url: trimmedBaseUrl,
          api_key: trimmedKey,
          model: trimmedModel,
          is_enabled: isEnabled,
          timeout_seconds: parsedTimeout,
        })
        addToast({ type: 'success', message: 'AI 服务商修改成功' })
      } else {
        await createAiProvider({
          name: trimmedName,
          base_url: trimmedBaseUrl,
          api_key: trimmedKey,
          model: trimmedModel,
          is_enabled: isEnabled,
          timeout_seconds: parsedTimeout,
        })
        addToast({ type: 'success', message: 'AI 服务商创建成功' })
      }
      onSaved()
      onClose()
    } catch (error) {
      addToast({
        type: 'error',
        message: getApiErrorMessage(error, editing ? '修改 AI 服务商失败' : '创建 AI 服务商失败'),
      })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div
      className="modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-label={editing ? '修改 AI 服务商' : '新建 AI 服务商'}
    >
      <div className="modal-content max-w-lg">
        <form onSubmit={handleSubmit}>
          <div className="modal-header">
            <div>
              <h2 className="modal-title">{editing ? '修改 AI 服务商' : '新建 AI 服务商'}</h2>
              <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
                {editing
                  ? '修改服务商连接信息；如无需更改 API Key 请保持掩码不变'
                  : '配置新的 AI 服务商信息，商品发布时将按优先级依次尝试调用'}
              </p>
            </div>
            <button
              type="button"
              className="modal-close"
              onClick={onClose}
              disabled={saving}
              aria-label="关闭"
            >
              <X className="h-5 w-5" />
            </button>
          </div>

          <div className="modal-body space-y-4">
            <div className="input-group">
              <label className="input-label">
                服务商名称 <span className="text-red-500">*</span>
              </label>
              <input
                className="input-ios"
                value={name}
                onChange={(e) => setName(e.target.value)}
                maxLength={80}
                placeholder="请输入服务商名称"
                disabled={saving}
              />
            </div>

            <div className="input-group">
              <label className="input-label">
                Base URL <span className="text-red-500">*</span>
              </label>
              <input
                className="input-ios"
                value={baseUrl}
                onChange={(e) => setBaseUrl(e.target.value)}
                maxLength={300}
                placeholder="请输入接口基础地址（Base URL）"
                disabled={saving}
              />
            </div>

            <div className="input-group">
              <label className="input-label">
                API Key{' '}
                {editing ? (
                  <span className="text-xs font-normal text-slate-400">（留空或保持掩码表示不修改）</span>
                ) : (
                  <span className="text-red-500">*</span>
                )}
              </label>
              <PasswordInput
                value={apiKey}
                onChange={setApiKey}
                placeholder={editing ? '留空或保持原掩码表示不修改' : '请输入 API Key'}
                disabled={saving}
              />
            </div>

            <div className="input-group">
              <label className="input-label">
                模型名 <span className="text-red-500">*</span>
              </label>
              <input
                className="input-ios"
                value={model}
                onChange={(e) => setModel(e.target.value)}
                maxLength={100}
                placeholder="请输入模型名称"
                disabled={saving}
              />
            </div>

            <div className="input-group">
              <label className="input-label">超时时间（秒）</label>
              <input
                type="number"
                className="input-ios"
                min={1}
                max={300}
                value={timeoutSeconds}
                onChange={(e) => setTimeoutSeconds(e.target.value)}
                placeholder="默认 30，范围 1-300"
                disabled={saving}
              />
            </div>

            <div className="flex items-center justify-between pt-1">
              <div>
                <label className="input-label mb-0">是否启用</label>
                <p className="text-xs text-slate-400 dark:text-slate-500">停用后容灾调度将跳过此服务商</p>
              </div>
              <label className="switch-ios">
                <input
                  type="checkbox"
                  checked={isEnabled}
                  onChange={(e) => setIsEnabled(e.target.checked)}
                  disabled={saving}
                />
                <span className="switch-slider" />
              </label>
            </div>
          </div>

          <div className="modal-footer">
            <button
              type="button"
              className="btn-ios-secondary"
              onClick={onClose}
              disabled={saving}
            >
              取消
            </button>
            <button
              type="submit"
              className="btn-ios-primary"
              disabled={saving}
            >
              {saving && <Loader2 className="h-4 w-4 animate-spin" />}
              {editing ? '保存修改' : '创建服务商'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
