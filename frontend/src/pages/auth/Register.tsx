import { useState, useEffect } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { MessageSquare, User, Lock, Mail, Phone, Eye, EyeOff } from 'lucide-react'
import { AuthNavbar } from '@/components/common/AuthNavbar'
import { SafeHtml } from '@/components/common/SafeHtml'
import { getDefaultAuthFooterAdSettings } from '@/api/settings'
import { register, getAuthFooterAdSettings } from '@/api/auth'
import { useUIStore } from '@/store/uiStore'
import { ButtonLoading } from '@/components/common/Loading'
import { RegistrationGate } from './RegistrationGate'
import { useRegistrationStatus } from './useRegistrationStatus'

export function Register() {
  const navigate = useNavigate()
  const { addToast } = useUIStore()

  const [loading, setLoading] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const { enabled: registrationEnabled, error: registrationError, reload: reloadRegistration } = useRegistrationStatus()
  const [authFooterAd, setAuthFooterAd] = useState(() => getDefaultAuthFooterAdSettings())

  // Form states
  const [username, setUsername] = useState('')
  const [phone, setPhone] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')

  useEffect(() => {
    getAuthFooterAdSettings()
      .then((result) => setAuthFooterAd(result))
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (registrationEnabled !== false) return
    addToast({ type: 'warning', message: '注册功能已关闭' })
    const timer = setTimeout(() => navigate('/login'), 1500)
    return () => clearTimeout(timer)
  }, [registrationEnabled, navigate, addToast])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()

    const trimmedUsername = username.trim()
    const trimmedPhone = phone.trim()
    const trimmedEmail = email.trim()

    if (!trimmedUsername) {
      addToast({ type: 'warning', message: '请输入用户名' })
      return
    }

    if (trimmedPhone && !/^1[3-9]\d{9}$/.test(trimmedPhone)) {
      addToast({ type: 'warning', message: '请输入正确的手机号格式' })
      return
    }

    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
    if (trimmedEmail && !emailRegex.test(trimmedEmail)) {
      addToast({ type: 'warning', message: '请输入正确的邮箱格式' })
      return
    }

    if (!password) {
      addToast({ type: 'warning', message: '请输入密码' })
      return
    }

    if (password.length < 6) {
      addToast({ type: 'warning', message: '密码长度至少6位' })
      return
    }

    if (!confirmPassword) {
      addToast({ type: 'warning', message: '请确认密码' })
      return
    }

    if (password !== confirmPassword) {
      addToast({ type: 'warning', message: '两次输入的密码不一致' })
      return
    }

    setLoading(true)

    try {
      const result = await register({
        username: trimmedUsername,
        phone: trimmedPhone || undefined,
        email: trimmedEmail || undefined,
        password,
      })

      if (result.success) {
        addToast({ type: 'success', message: '注册成功，请登录' })
        navigate('/login')
      } else {
        addToast({ type: 'error', message: result.message || '注册失败' })
      }
    } catch (error: unknown) {
      // 获取后端返回的错误信息
      const err = error as { response?: { data?: { detail?: string; message?: string } } }
      const errorMsg = err?.response?.data?.detail || err?.response?.data?.message || '注册失败，请检查网络连接'
      addToast({ type: 'error', message: errorMsg })
    } finally {
      setLoading(false)
    }
  }

  if (registrationEnabled === null) {
    return <RegistrationGate error={registrationError} reload={reloadRegistration} />
  }

  if (registrationEnabled === false) {
    return (
      <div className="min-h-screen bg-slate-50 dark:bg-slate-900 flex items-center justify-center p-4">
        <div className="bg-white dark:bg-slate-800 rounded-lg shadow-sm border border-slate-200 dark:border-slate-700 p-8 text-center max-w-sm">
          <div className="w-14 h-14 rounded-full bg-amber-100 dark:bg-amber-900/30 mx-auto mb-4 flex items-center justify-center">
            <span className="text-2xl">🚫</span>
          </div>
          <h1 className="text-lg vben-card-title text-slate-900 dark:text-slate-100 mb-2">注册功能已关闭</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400 mb-6">管理员已关闭注册功能，如需账号请联系管理员</p>
          <Link to="/login" className="btn-ios-primary">
            返回登录
          </Link>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-slate-50 dark:bg-slate-900 transition-colors">
      <AuthNavbar />
      <div className="pt-20 pb-10 px-4 sm:px-6 flex items-start justify-center min-h-screen">
      <div className="w-full max-w-md">
        {/* Mobile header */}
        <div className="text-center mb-6">
          <div className="w-12 h-12 rounded-xl bg-blue-600 text-white mx-auto mb-4 flex items-center justify-center">
            <MessageSquare className="w-6 h-6" />
          </div>
          <h1 className="text-xl font-bold text-slate-900 dark:text-slate-100">用户注册</h1>
          <p className="text-sm text-slate-500 dark:text-slate-400">创建您的账号以开始使用</p>
        </div>

        {/* Register Card */}
        <div className="bg-white dark:bg-slate-800 rounded-lg shadow-sm border border-slate-200 dark:border-slate-700 p-6">
          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Username */}
            <div className="input-group">
              <label className="input-label">用户名</label>
              <div className="relative">
                <User className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                <input
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  placeholder="请输入用户名"
                  className="input-ios pl-9"
                />
              </div>
            </div>

            {/* Phone */}
            <div className="input-group">
              <label className="input-label">手机号 <span className="text-xs text-slate-400 font-normal">（可选）</span></label>
              <div className="relative">
                <Phone className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                <input
                  type="tel"
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  placeholder="请输入手机号"
                  className="input-ios pl-9"
                />
              </div>
            </div>

            {/* Email */}
            <div className="input-group">
              <label className="input-label">邮箱地址 <span className="text-xs text-slate-400 font-normal">（可选）</span></label>
              <div className="relative">
                <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="name@example.com"
                  className="input-ios pl-9"
                />
              </div>
            </div>

            {/* Password */}
            <div className="input-group">
              <label className="input-label">密码</label>
              <div className="relative">
                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                <input
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="至少6位字符"
                  className="input-ios pl-9 pr-9"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 dark:hover:text-slate-300"
                >
                  {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                </button>
              </div>
            </div>

            {/* Confirm Password */}
            <div className="input-group">
              <label className="input-label">确认密码</label>
              <div className="relative">
                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                <input
                  type={showPassword ? 'text' : 'password'}
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                  placeholder="请再次输入密码"
                  className="input-ios pl-9"
                />
              </div>
            </div>

            {/* Submit button */}
            <button
              type="submit"
              disabled={loading}
              className="w-full btn-ios-primary"
            >
              {loading ? <ButtonLoading /> : '注 册'}
            </button>
          </form>

          {/* Login link */}
          <p className="text-center mt-6 text-slate-500 dark:text-slate-400 text-sm">
            已有账号？{' '}
            <Link to="/login" className="text-blue-600 dark:text-blue-400 font-medium hover:text-indigo-700">
              立即登录
            </Link>
          </p>
        </div>

        {/* Footer */}
        <SafeHtml
          html={authFooterAd['auth.footer_ad_html']}
          className="mt-6 text-center text-xs text-slate-400 dark:text-slate-500"
        />
      </div>
      </div>
    </div>
  )
}
