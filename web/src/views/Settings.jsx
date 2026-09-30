import { useEffect, useState } from 'react'
import { PALETTES, setPalette, usePalette } from '../theme'
import { useTranslation, Trans } from 'react-i18next'
import { LANGS, setLang } from '../i18n'
import QRCode from 'qrcode'
import { api, setToken, toast, useStore } from '../store'
import Mascot from '../components/Mascot'
import { go } from '../App'
import Channels from './Channels'
import Devices from './Devices'
import Connectors from './Connectors'
import Apps from './Apps'
import Usage from './Usage'
import Skills from './Skills'
import AgentIds from './AgentIds'
import Boundary from '../components/Boundary'

// #/settings/<tab>: general stuff plus the ways your team connects to the rest of your life
const TABS = [
  ['general', '⚙️'],
  ['apps', '🧩'],
  ['usage', '📊'],
  ['identity', '🪪'],
  ['channels', '💬'],
  ['devices', '💻'],
  ['skills', '📚'],
  ['advanced', '🛠️'],
]

export default function Settings({ sub }) {
  const { t } = useTranslation('settings')
  const want = sub === 'connectors' ? 'apps' : sub // old links
  const tab = TABS.find((x) => x[0] === want) ? want : 'general'
  return (
    <div>
      <header className="page-head">
        <div>
          <h1>{t('title')}</h1>
          <p className="muted">{t('lead')} <a href="#/about">{t('common:nav.whatIs')} ✨</a></p>
        </div>
      </header>
      <div className="hub-tabs">
        {TABS.map(([k, icon]) => (
          <button key={k} className={tab === k ? 'on' : ''} onClick={() => { location.hash = `#/settings/${k}` }}>{icon} {t(`tabs.${k}`)}</button>
        ))}
      </div>
      <Boundary key={tab}>
        {tab === 'general' && <General />}
        {tab === 'identity' && <AgentIds />}
        {tab === 'channels' && <Channels />}
        {tab === 'devices' && <Devices />}
        {tab === 'apps' && <Apps />}
        {tab === 'usage' && <Usage />}
        {tab === 'advanced' && <Connectors />}
        {tab === 'skills' && <Skills />}
      </Boundary>
    </div>
  )
}

function General() {
  const { t, i18n } = useTranslation('settings')
  const { model, connected } = useStore()
  const [pair, setPair] = useState(null)
  const [qr, setQr] = useState('')
  const [theme, setTheme] = useState(() => document.documentElement.dataset.theme || 'auto')
  const palette = usePalette()

  useEffect(() => {
    if (!pair) return
    QRCode.toDataURL(pair.url, { margin: 1, width: 220, color: { dark: '#2B2233', light: '#FFFFFF' } }).then(setQr)
  }, [pair])

  const newCode = async () => setPair(await api('/api/pair/new', { method: 'POST', body: { origin: location.origin } }))
  const setT = (t) => {
    setTheme(t)
    if (t === 'auto') delete document.documentElement.dataset.theme
    else document.documentElement.dataset.theme = t
    try { localStorage.setItem('dot_theme', t === 'auto' ? '' : t) } catch { /* ignore */ }
  }

  return (
    <div>
      <div className="cards">
        <div className="card">
          <h3>🌐 Language / 语言</h3>
          <div className="seg">
            {LANGS.map(([code, label]) => (
              <button key={code} className={i18n.language === code ? 'on' : ''} onClick={() => setLang(code, api)}>{label}</button>
            ))}
          </div>
        </div>
        <div className="card">
          <h3>📱 {t('pair.title')}</h3>
          <p className="muted small">{t('pair.hint')}</p>
          {pair ? (
            <div className="pair-box">
              {qr && <img src={qr} alt={t('pair.qrAlt')} />}
              <code className="big-code">{pair.code}</code>
            </div>
          ) : (
            <button className="btn" onClick={newCode}>{t('pair.show')}</button>
          )}
        </div>
        <div className="card">
          <h3>🧠 {t('brain.title')}</h3>
          <p className="small">{t('brain.defaultModel')} <code>{model}</code></p>
          <p className="muted small"><Trans t={t} i18nKey="brain.hint" components={{ code: <code /> }} /></p>
          <p className="small">{t('brain.liveLink')} {connected ? `🟢 ${t('brain.connected')}` : `🟠 ${t('common:status.reconnecting')}`}</p>
        </div>
        <Profiles />
        <SecondLook />
        <div className="card">
          <h3>🎨 {t('look.title')}</h3>
          <div className="seg">
            {['auto', 'light', 'dark'].map((m) => (
              <button key={m} className={theme === m ? 'on' : ''} onClick={() => setT(m)}>{t(`look.${m}`)}</button>
            ))}
          </div>
          <div className="palettes mt8">
            {PALETTES.map((pl) => (
              <button key={pl.key} className={`palette ${palette === pl.key ? 'on' : ''}`} onClick={() => setPalette(pl.key)}>
                <span className="palette-sw">{pl.swatch.map((c) => <i key={c} style={{ background: c }} />)}</span>
                <small>{t(`look.palette.${pl.key}`)}</small>
              </button>
            ))}
          </div>
          <div className="row gap8 mt8 center">
            {['idle', 'thinking', 'working', 'waiting', 'sleeping'].map((s) => (
              <div key={s} className="col center"><Mascot animal="fox" color="#FFB38A" status={s} size={44} /><small className="muted">{t(`common:status.${s}`)}</small></div>
            ))}
          </div>
        </div>
        <div className="card">
          <h3>🚪 {t('device.title')}</h3>
          <p className="muted small">{t('device.hint')}</p>
          <div className="col gap6">
            <button className="btn ghost" onClick={() => go('team')}>👥 {t('device.openTeam')}</button>
            <button className="btn danger" onClick={() => { setToken(''); location.reload() }}>{t('device.signOut')}</button>
          </div>
        </div>
      </div>
    </div>
  )
}

// a quick extra check before an agent acts outside or speaks for you
function SecondLook() {
  const { t } = useTranslation('settings')
  const [s, setS] = useState(null)
  useEffect(() => { api('/api/settings/reviewer').then(setS).catch(() => {}) }, [])
  const set = async (on) => setS(await api('/api/settings/reviewer', { method: 'PUT', body: { on } }))
  if (!s) return null
  return (
    <div className="card">
      <h3>🛡️ {t('secondLook.title')}</h3>
      <p className="muted small">{t('secondLook.hint')}</p>
      <div className="seg">
        {[true, false].map((on) => (
          <button key={String(on)} className={s.on === on ? 'on' : ''} disabled={s.locked} onClick={() => set(on)}>{t(on ? 'secondLook.on' : 'secondLook.off')}</button>
        ))}
      </div>
      {s.locked && <p className="muted small mt8">{t('secondLook.locked')}</p>}
    </div>
  )
}

function Profiles() {
  const { t } = useTranslation('settings')
  const [profiles, setProfiles] = useState([])
  const [form, setForm] = useState(null)
  const [testing, setTesting] = useState(null)
  const [providers, setProviders] = useState([])
  const [provider, setProvider] = useState('')
  const [models, setModels] = useState(null) // live list from the provider, or null
  const [listing, setListing] = useState(false)
  const load = () => api('/api/profiles').then((r) => setProfiles(r.profiles)).catch(() => {})
  useEffect(() => {
    load()
    api('/api/profiles/providers').then(setProviders).catch(() => {})
  }, [])
  const openForm = (f) => {
    setForm(f)
    setModels(null)
    setProvider('')
  }
  const listModels = async () => {
    setListing(true)
    try {
      const r = await api('/api/profiles/models', { method: 'POST', body: { provider, api_key: form.api_key, base_url: provider === 'custom' ? form.base_url : '', name: form.name } })
      if (!r.ok) toast(t('profiles.listFailed', { msg: r.error }))
      else {
        setModels(r.models)
        setForm((f) => ({ ...f, base_url: r.base_url || (provider === 'custom' ? f.base_url : ''), model: r.models.includes(f.model) ? f.model : r.models[0] || f.model }))
      }
    } catch (e) {
      toast(t('profiles.listFailed', { msg: e.message }))
    }
    setListing(false)
  }

  const test = async (p) => {
    setTesting(p.name)
    try {
      const r = await api('/api/profiles/test', { method: 'POST', body: p })
      toast(r.ok ? `✅ ${p.name}: ${r.reply}` : `😵 ${p.name}: ${r.error}`)
    } catch (e) {
      toast(t('profiles.testFailed', { msg: e.message }))
    }
    setTesting(null)
  }
  const del = async (name) => {
    try {
      await api(`/api/profiles/${name}`, { method: 'DELETE' })
      load()
    } catch (e) {
      toast(e.message)
    }
  }

  return (
    <div className="card">
      <h3>🧩 {t('profiles.title')}</h3>
      <p className="muted small">{t('profiles.hint')}</p>
      <div className="col gap6">
        {profiles.map((p) => (
          <div key={p.name} className="row between gap6">
            <div className="grow">
              <b>{p.name}</b> {p.builtin && <small className="muted">{t('profiles.builtin')}</small>}
              <div className="muted small ellipsis">{p.model || '—'} · {p.base_url || '—'}</div>
            </div>
            <button className="btn ghost sm" disabled={testing === p.name} onClick={() => test(p)}>{testing === p.name ? '…' : t('profiles.test')}</button>
            <button className="btn ghost sm" onClick={() => openForm(p)}>{t('common:edit')}</button>
            {!p.builtin && <button className="btn ghost sm" onClick={() => del(p.name)}>{t('common:delete')}</button>}
          </div>
        ))}
      </div>
      <button className="btn ghost sm mt8" onClick={() => openForm({ name: '', base_url: '', api_key: '', model: '', reasoning_effort: '', max_tokens: 16384 })}>＋ {t('profiles.new')}</button>

      {form && (
        <div className="modal-bg" onClick={() => setForm(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>{form.builtin ? t('profiles.editTitle', { name: form.name }) : t('profiles.newTitle')}</h2>
            <label className="lbl">{t('profiles.name')}</label>
            <input className="input" disabled={!!form.builtin} placeholder={t('profiles.namePh')} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <label className="lbl">{t('profiles.provider')}</label>
            <select className="input" value={provider} onChange={(e) => { setProvider(e.target.value); setModels(null) }}>
              <option value="">{t('profiles.providerPick')}</option>
              {providers.map((p) => (
                <option key={p.id} value={p.id}>{p.label}</option>
              ))}
            </select>
            {provider && providers.find((p) => p.id === provider)?.keys && (
              <a className="muted small" href={providers.find((p) => p.id === provider).keys} target="_blank" rel="noreferrer">{t('profiles.getKey')} ↗</a>
            )}
            {(provider === 'custom' || (!provider && form.base_url)) && (
              <>
                <label className="lbl">{t('profiles.baseUrl')}</label>
                <input className="input mono" placeholder="https://api.example.com/v1" value={form.base_url} onChange={(e) => setForm({ ...form, base_url: e.target.value })} />
              </>
            )}
            <label className="lbl">{t('profiles.apiKey', { vault: '{{vault:NAME}}' })}</label>
            <input className="input mono" type="password" value={form.api_key} onChange={(e) => setForm({ ...form, api_key: e.target.value })} />
            <label className="lbl">{t('profiles.model')}</label>
            <div className="row gap6">
              {models ? (
                <select className="input mono grow" value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })}>
                  {!models.includes(form.model) && form.model && <option value={form.model}>{form.model}</option>}
                  {models.map((m) => (
                    <option key={m} value={m}>{m}</option>
                  ))}
                </select>
              ) : (
                <input className="input mono grow" placeholder={t('profiles.modelPh')} value={form.model} onChange={(e) => setForm({ ...form, model: e.target.value })} />
              )}
              <button className="btn ghost sm" disabled={!provider || listing} onClick={listModels}>{listing ? '…' : t('profiles.listModels')}</button>
            </div>
            <label className="lbl">{t('profiles.effort')}</label>
            <input className="input mono" placeholder="low" value={form.reasoning_effort} onChange={(e) => setForm({ ...form, reasoning_effort: e.target.value })} />
            <div className="row end gap8">
              <button className="btn ghost" onClick={() => setForm(null)}>{t('common:cancel')}</button>
              <button
                className="btn"
                disabled={!form.name}
                onClick={async () => {
                  try {
                    await api('/api/profiles', { method: 'PUT', body: form })
                    setForm(null)
                    load()
                  } catch (e) {
                    toast(t('profiles.saveFailed', { msg: e.message }))
                  }
                }}
              >
                {t('common:save')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
