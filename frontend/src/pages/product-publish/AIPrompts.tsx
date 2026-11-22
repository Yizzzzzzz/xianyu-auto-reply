/**
 * AI 提示词管理页面
 *
 * 功能：
 * 1. 三条提示词分别说明用途并可编辑：
 *    - 发布文案提示词：发布第 2 步自动生成标题 / 详情 / 分类 / 搜索词
 *    - 挑同款提示词：发布第 4 步挑最相关同款并推断建议价
 *    - 拍照识物提示词：拍照识物第一步结构化识别物品名称 / 分类 / 介绍 / 搜索词
 * 2. 保存（只提交改过的字段）、恢复默认（二次确认）
 * 3. 显示最后修改时间与是否为默认提示词；保存失败显示后端返回的明确错误
 * 4. 页面上绝不展示任何 API Key
 */
import { useCallback, useEffect, useState } from 'react'
import { Loader2, MessageSquareText, RotateCcw, Save } from 'lucide-react'
import {
  getAiPrompts,
  resetAiPrompts,
  updateAiPrompts,
  type AiPrompts,
} from '@/api/aiPrompts'
import { ConfirmModal } from '@/components/common/ConfirmModal'
import { Loading } from '@/components/common/Loading'
import { useUIStore } from '@/store/uiStore'
import { getApiErrorMessage } from '@/utils/apiError'

const formatDateTime = (value?: string | null): string => {
  if (!value) return '未修改过'
  return value.replace('T', ' ').slice(0, 19)
}

export function AIPrompts() {
  const { addToast } = useUIStore()
  const [prompts, setPrompts] = useState<AiPrompts | null>(null)
  const [listingDraft, setListingDraft] = useState('')
  const [pickDraft, setPickDraft] = useState('')
  const [identifyDraft, setIdentifyDraft] = useState('')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [showResetConfirm, setShowResetConfirm] = useState(false)
  const [resetting, setResetting] = useState(false)

  const applyPrompts = useCallback((data: AiPrompts) => {
    setPrompts(data)
    setListingDraft(data.listing_prompt)
    setPickDraft(data.pick_prompt)
    setIdentifyDraft(data.identify_prompt)
  }, [])

  const loadPrompts = useCallback(async () => {
    setLoading(true)
    try {
      const data = await getAiPrompts()
      applyPrompts(data)
    } catch (error) {
      setPrompts(null)
      addToast({ type: 'error', message: getApiErrorMessage(error, '获取 AI 提示词失败') })
    } finally {
      setLoading(false)
    }
  }, [addToast, applyPrompts])

  useEffect(() => {
    void loadPrompts()
  }, [loadPrompts])

  const listingChanged = prompts !== null && listingDraft !== prompts.listing_prompt
  const pickChanged = prompts !== null && pickDraft !== prompts.pick_prompt
  const identifyChanged = prompts !== null && identifyDraft !== prompts.identify_prompt
  const hasChanges = listingChanged || pickChanged || identifyChanged

  const handleSave = async () => {
    if (!prompts || !hasChanges) return
    setSaving(true)
    try {
      const params: { listing_prompt?: string; pick_prompt?: string; identify_prompt?: string } = {}
      if (listingChanged) params.listing_prompt = listingDraft
      if (pickChanged) params.pick_prompt = pickDraft
      if (identifyChanged) params.identify_prompt = identifyDraft
      const data = await updateAiPrompts(params)
      applyPrompts(data)
      addToast({ type: 'success', message: 'AI 提示词已保存，下一次 AI 调用即生效' })
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '保存 AI 提示词失败') })
    } finally {
      setSaving(false)
    }
  }

  const handleReset = async () => {
    setResetting(true)
    try {
      const data = await resetAiPrompts()
      applyPrompts(data)
      setShowResetConfirm(false)
      addToast({ type: 'success', message: '已恢复默认 AI 提示词' })
    } catch (error) {
      addToast({ type: 'error', message: getApiErrorMessage(error, '恢复默认提示词失败') })
    } finally {
      setResetting(false)
    }
  }

  return (
    <div className="flex min-h-0 flex-col space-y-3 sm:space-y-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <h1 className="page-title">AI 提示词</h1>
          <p className="page-description">
            三条提示词只在后端定义、保存后下一次 AI 调用即生效；修改前请确认模型输出的 JSON 字段名还在提示词里，否则发布流程无法解析模型返回。
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            className="btn-ios-secondary"
            disabled={!prompts || resetting}
            onClick={() => setShowResetConfirm(true)}
          >
            <RotateCcw className="h-4 w-4" />
            恢复默认
          </button>
          <button
            type="button"
            className="btn-ios-primary"
            disabled={!hasChanges || saving}
            onClick={() => void handleSave()}
          >
            {saving && <Loader2 className="h-4 w-4 animate-spin" />}
            <Save className="h-4 w-4" />
            保存
          </button>
        </div>
      </div>

      <div className="vben-card relative flex min-h-[440px] flex-col overflow-hidden p-4 sm:p-5">
        {loading && (
          <div className="absolute inset-0 z-20 flex items-center justify-center bg-white/80 backdrop-blur-sm dark:bg-slate-900/80">
            <Loading text="正在加载 AI 提示词..." />
          </div>
        )}

        {!loading && prompts && (
          <div className="flex flex-col gap-5">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-500 dark:text-slate-400">
              <span>最后修改：{formatDateTime(prompts.updated_at)}</span>
              <span>
                发布文案提示词：{prompts.listing_is_default ? '默认' : '已自定义'}
              </span>
              <span>
                挑同款提示词：{prompts.pick_is_default ? '默认' : '已自定义'}
              </span>
              <span>
                拍照识物提示词：{prompts.identify_is_default ? '默认' : '已自定义'}
              </span>
            </div>

            <div>
              <div className="mb-2 flex items-center gap-2">
                <MessageSquareText className="h-4 w-4 text-slate-500 dark:text-slate-400" />
                <h2 className="text-sm font-medium text-slate-900 dark:text-slate-100">
                  发布文案提示词
                </h2>
                {!prompts.listing_is_default && (
                  <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-700 dark:bg-amber-900/40 dark:text-amber-300">
                    已自定义
                  </span>
                )}
              </div>
              <p className="mb-2 text-xs text-slate-500 dark:text-slate-400">
                用在发布第 2 步：根据商品图片自动生成标题、详情、分类和搜同款用的搜索词。必须包含
                title、description、category、search_keyword 四个词，否则模型输出无法解析。
              </p>
              <textarea
                className="input-ios min-h-64 font-mono text-xs leading-5"
                rows={14}
                value={listingDraft}
                onChange={(event) => setListingDraft(event.target.value)}
                placeholder="发布文案生成提示词"
              />
            </div>

            <div>
              <div className="mb-2 flex items-center gap-2">
                <MessageSquareText className="h-4 w-4 text-slate-500 dark:text-slate-400" />
                <h2 className="text-sm font-medium text-slate-900 dark:text-slate-100">
                  挑同款定价提示词
                </h2>
                {!prompts.pick_is_default && (
                  <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-700 dark:bg-amber-900/40 dark:text-amber-300">
                    已自定义
                  </span>
                )}
              </div>
              <p className="mb-2 text-xs text-slate-500 dark:text-slate-400">
                用在发布第 4 步：对照商品图片，从候选同款中挑最相关的一条并推断建议价。必须包含
                index、suggested_price、reason 三个词和 {'{candidates}'} 占位符（调用时由系统填入候选同款列表）。
              </p>
              <textarea
                className="input-ios min-h-48 font-mono text-xs leading-5"
                rows={11}
                value={pickDraft}
                onChange={(event) => setPickDraft(event.target.value)}
                placeholder="挑同款定价提示词"
              />
            </div>

            <div>
              <div className="mb-2 flex items-center gap-2">
                <MessageSquareText className="h-4 w-4 text-slate-500 dark:text-slate-400" />
                <h2 className="text-sm font-medium text-slate-900 dark:text-slate-100">
                  拍照识物提示词
                </h2>
                {!prompts.identify_is_default && (
                  <span className="rounded bg-amber-100 px-1.5 py-0.5 text-xs text-amber-700 dark:bg-amber-900/40 dark:text-amber-300">
                    已自定义
                  </span>
                )}
              </div>
              <p className="mb-2 text-xs text-slate-500 dark:text-slate-400">
                用在拍照识物第一步：结构化识别物品名称、分类、介绍和搜同款比价的搜索词。必须包含
                name、category、description、search_keyword 四个词，否则模型输出无法解析。
              </p>
              <textarea
                className="input-ios min-h-64 font-mono text-xs leading-5"
                rows={14}
                value={identifyDraft}
                onChange={(event) => setIdentifyDraft(event.target.value)}
                placeholder="拍照识物提示词"
              />
            </div>

            <p className="text-xs text-slate-400 dark:text-slate-500">
              提示：只保存改过的提示词；恢复默认会同时把三条提示词恢复为系统内置版本。
            </p>
          </div>
        )}
      </div>

      {showResetConfirm && (
        <ConfirmModal
          isOpen={showResetConfirm}
          title="恢复默认提示词"
          message={
            <span>
              确定要把三条 AI 提示词都恢复为系统默认版本吗？自定义内容将被删除，不可恢复。
            </span>
          }
          type="danger"
          confirmText="确定恢复"
          cancelText="取消"
          loading={resetting}
          onConfirm={() => void handleReset()}
          onCancel={() => setShowResetConfirm(false)}
        />
      )}
    </div>
  )
}

export default AIPrompts
