import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, toast, useStore } from '../store'
import './Channels.css'

function StatusPill({ status }) {
  const { t } = useTranslation('channels')
  const map = {
    connected: ['ok', '🟢'],
    connecting: ['', '🟡'],
    error: ['bad', '🔴'],
    disabled: ['', '⚪'],
  }
  const key = map[status] ? status : 'disabled'
  const [cls, icon] = map[key]
  return <span className={`pill ${cls}`}>{icon} {t(`status.${key}`)}</span>
}

function ChannelCard({ ch, onChange }) {
  const { t } = useTranslation('channels')
  const [open, setOpen] = useState(false)
  const [form, setForm] = useState(ch.config)
  const [home, setHome] = useState(ch.home)
  const [pairCode, setPairCode] = useState('')
  const [bindings, setBindings] = useState(null)
  const [allow, setAllow] = useState(null)
  const [qr, setQr] = useState(null)

  useEffect(() => setForm(ch.config), [ch.config])

  const save = async () => {
    const vaulted = {}
    for (const f of ch.fields) {
      const v = form[f.key] || ''
      if (f.secret && v && !v.startsWith('{{vault:')) {
        await api('/api/vault', { method: 'POST', body: { name: f.key, value: v } })
        vaulted[f.key] = `{{vault:${f.key}}}`
      } else vaulted[f.key] = v
    }
    await api(`/api/channels/${ch.name}/config`, { method: 'PUT', body: vaulted })
    toast(`${ch.emoji} ${t('toast.saved')}`)
    setTimeout(onChange, 2500)
  }

  const restart = async () => {
    await api(`/api/channels/${ch.name}/restart`, { method: 'POST' })
    toast(t('toast.reconnecting'))
    setTimeout(onChange, 2000)
  }

  const newCode = async () => {
    const r = await api(`/api/channels/${ch.name}/pair/new`, { method: 'POST' })
    setPairCode(r.code)
  }

  const saveHome = async (chatId) => {
    setHome(chatId)
    await api(`/api/channels/${ch.name}/home`, { method: 'PUT', body: { chat_id: chatId } })
  }

  const loadBindings = async () => {
    setBindings(await api(`/api/channels/${ch.name}/bindings`))
    setAllow(await api(`/api/channels/${ch.name}/allowlist`))
  }

  const unbind = async (id) => {
    await api(`/api/channels/${ch.name}/bindings/${id}`, { method: 'DELETE' })
    loadBindings()
  }
  const unallow = async (ident) => {
    await api(`/api/channels/${ch.name}/allowlist/${encodeURIComponent(ident)}`, { method: 'DELETE' })
    loadBindings()
  }

  const startQr = async () => {
    const r = await api('/api/channels/wechat/qr', { method: 'POST' })
    setQr(r)
  }

  useEffect(() => {
    if (ch.name !== 'wechat' || !qr) return
    const timer = setInterval(async () => {
      const s = await api('/api/channels/wechat/qr/status')
      if (s.status === 'confirmed') {
        clearInterval(timer)
        setQr(null)
        toast(`🟢 ${t('toast.wechatPaired')}`)
        onChange()
      } else if (s.status === 'expired') {
        clearInterval(timer)
        setQr(null)
        toast(t('toast.qrExpired'))
      }
    }, 2500)
    return () => clearInterval(timer)
  }, [qr, ch.name])

  return (
    <div className={`card channel-card ${ch.status}`}>
      <div className="row between">
        <div className="row gap8">
          <span className="channel-emoji">{ch.emoji}</span>
          <div>
            <h3>{ch.label}</h3>
            <StatusPill status={ch.status} />
          </div>
        </div>
        <button className="btn ghost sm" onClick={() => { setOpen(!open); if (!open) loadBindings() }}>
          {open ? t('common:close') : t('setUp')}
        </button>
      </div>
      {ch.error && <p className="small" style={{ color: 'var(--bad)' }}>{ch.error}</p>}

      {open && (
        <div className="channel-body">
          <ol className="channel-steps small muted">
            {ch.setup.map((s, i) => <li key={i}>{s}</li>)}
          </ol>

          {ch.name === 'wechat' ? (
            <div className="row gap8 wrap" style={{ alignItems: 'flex-start' }}>
              <button className="btn sm" onClick={startQr}>📷 {t('wechat.getQr')}</button>
              {qr && (
                <div className="qr-box">
                  {qr.image_url
                    ? <img src={qr.image_url} alt={t('wechat.qrAlt')} width={160} height={160} />
                    : <p className="small mono">{qr.qrcode}</p>}
                  <p className="small muted">{t('wechat.scanHint')}</p>
                </div>
              )}
            </div>
          ) : (
            <>
              {ch.fields.map((f) => (
                <div key={f.key} className="row gap6 mt8">
                  <label className="small muted field-label">{f.label}</label>
                  <input className="input" type={f.secret ? 'password' : 'text'}
                    placeholder={f.secret ? '••••••••' : f.label}
                    value={form[f.key] === `{{vault:${f.key}}}` ? '' : (form[f.key] || '')}
                    onChange={(e) => setForm({ ...form, [f.key]: e.target.value })} />
                </div>
              ))}
              <div className="row gap6 mt8">
                <button className="btn sm" onClick={save}>{t('saveConnect')}</button>
                {ch.configured && <button className="btn ghost sm" onClick={restart}>{t('reconnect')}</button>}
              </div>
            </>
          )}

          <div className="section">
            <h4>🔑 {t('pairing.title')}</h4>
            <p className="small muted">{t('pairing.hintBefore')} <code>/pair CODE</code> {t('pairing.hintAfter')}</p>
            <div className="row gap6">
              <button className="btn ghost sm" onClick={newCode}>{t('pairing.newCode')}</button>
              {pairCode && <span className="pill">{pairCode}</span>}
            </div>
            {allow && allow.length > 0 && (
              <div className="row gap6 wrap mt8">
                {allow.map((a) => (
                  <span key={a} className="pill">{a} <button className="x" onClick={() => unallow(a)}>✕</button></span>
                ))}
              </div>
            )}
          </div>

          <div className="section">
            <h4>🏠 {t('home.title')}</h4>
            <input className="input" placeholder={t('home.placeholder')} value={home || ''}
              onChange={(e) => saveHome(e.target.value)} />
          </div>

          {bindings && bindings.length > 0 && (
            <div className="section">
              <h4>💬 {t('bound.title')}</h4>
              {bindings.map((b) => (
                <div key={b.id} className="row between binding-row">
                  <span className="small">{b.title || b.chat_id}</span>
                  <button className="btn ghost sm" onClick={() => unbind(b.id)}>{t('bound.unbind')}</button>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

export default function Channels() {
  const { t } = useTranslation('channels')
  const [channels, setChannels] = useState(null)
  const load = () => api('/api/channels').then(setChannels)
  useEffect(() => {
    load()
    const onEv = () => load()
    window.addEventListener('dot:channel', onEv)
    return () => window.removeEventListener('dot:channel', onEv)
  }, [])

  if (!channels) return null
  return (
    <div>
      <header className="page-head">
        <div>
          <h1>💬 {t('title')}</h1>
          <p className="muted">{t('lead')}</p>
        </div>
      </header>
      <div className="channels-grid">
        {channels.map((ch) => <ChannelCard key={ch.name} ch={ch} onChange={load} />)}
      </div>
    </div>
  )
}
