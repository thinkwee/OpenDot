import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, ChevronDown, Copy, ExternalLink, Loader2, Plus, Search, X } from 'lucide-react'
import { api, toast, useStore } from '../store'
import Mascot, { animalFor } from '../components/Mascot'
import AppIcon from '../components/AppIcon'
import { go } from '../App'
import { errOf, pick } from './idWizard'
import './Apps.css'

let wantCat = null

// Open Settings → Apps at one group of the directory (e.g. 'calendar').
export function openApps(cat) {
  wantCat = cat
  go('settings', 'apps')
  window.dispatchEvent(new CustomEvent('dot:apps-cat', { detail: cat }))
}

// Settings → Apps: what's connected, and a directory of apps to connect in one tap.
export default function Apps() {
  const { t, i18n } = useTranslation('apps')
  const lang = i18n.language
  const [d, setD] = useState(null)
  const [cat, setCat] = useState(() => { const c = wantCat || 'all'; wantCat = null; return c })
  useEffect(() => {
    const on = (e) => { setCat(e.detail); wantCat = null }
    window.addEventListener('dot:apps-cat', on)
    return () => window.removeEventListener('dot:apps-cat', on)
  }, [])
  const [q, setQ] = useState('')
  const [open, setOpen] = useState(null) // directory entry being connected
  const [custom, setCustom] = useState(false)
  const load = () => api('/api/apps').then(setD).catch(() => {})
  useEffect(() => {
    load()
    const on = () => load()
    const msg = (e) => e.data?.type === 'dot:app-signed-in' && setTimeout(load, 600)
    window.addEventListener('dot:connectors', on)
    window.addEventListener('message', msg)
    return () => {
      window.removeEventListener('dot:connectors', on)
      window.removeEventListener('message', msg)
    }
  }, [])
  // while something is waiting for a sign-in, keep an eye on it
  const waiting = d?.installed.some((a) => a.status === 'connecting' || (a.status === 'sign_in' && a.auth_url))
  useEffect(() => {
    if (!waiting) return
    const id = setInterval(load, 2500)
    return () => clearInterval(id)
  }, [waiting])

  const apps = useMemo(() => {
    if (!d) return []
    const s = q.trim().toLowerCase()
    return d.apps.filter((a) => (cat === 'all' || a.category === cat) &&
      (!s || `${a.name.en} ${a.name.zh} ${a.blurb.en} ${a.blurb.zh}`.toLowerCase().includes(s)))
  }, [d, cat, q])
  if (!d) return null
  const byApp = Object.fromEntries(d.apps.map((a) => [a.id, a]))

  return (
    <div className="apps-page">
      <section className="section">
        <h2 className="apps-h">{t('title')}</h2>
        <p className="muted">{t('lead')}</p>
        {d.installed.length > 0 ? (
          <div className="apps-installed">
            {d.installed.map((a) => (
              <Installed key={a.name} a={a} app={byApp[a.app]} lang={lang} reload={load} localRedirect={d.google.local_redirect} />
            ))}
          </div>
        ) : (
          <div className="apps-empty">{t('none')}</div>
        )}
      </section>

      <section className="section">
        <div className="row between wrap gap8 apps-dir-head">
          <h3>{t('directory')}</h3>
          <label className="apps-search">
            <Search size={15} />
            <input placeholder={t('search')} value={q} onChange={(e) => setQ(e.target.value)} />
          </label>
        </div>
        <div className="row gap6 wrap mb8">
          <button className={`chip ${cat === 'all' ? 'on' : ''}`} onClick={() => setCat('all')}>{t('all')}</button>
          {d.categories.map((c) => (
            <button key={c.id} className={`chip ${cat === c.id ? 'on' : ''}`} onClick={() => setCat(c.id)}>{pick(c.label, lang)}</button>
          ))}
        </div>
        <div className="apps-grid">
          {apps.map((a) => {
            const n = d.installed.filter((x) => x.app === a.id).length
            return (
              <button key={a.id} className="app-card" onClick={() => setOpen(a)}>
                <AppIcon app={a} size={42} />
                <div className="grow">
                  <div className="row gap6 app-card-top">
                    <b>{pick(a.name, lang)}</b>
                    {n > 0 && <span className="pill ok"><Check size={11} /> {t('added')}</span>}
                  </div>
                  <p className="small muted">{pick(a.blurb, lang)}</p>
                  <span className={`app-kind k-${a.kind}`}>{t(`kind.${a.kind}`)}</span>
                </div>
              </button>
            )
          })}
          <button className="app-card app-custom" onClick={() => setCustom(true)}>
            <span className="app-icon plus"><Plus size={20} /></span>
            <div className="grow">
              <b>{t('custom.title')}</b>
              <p className="small muted">{t('custom.blurb')}</p>
            </div>
          </button>
        </div>
      </section>

      {open && <ConnectSheet app={open} d={d} lang={lang} onClose={() => setOpen(null)} onDone={() => { setOpen(null); load() }} reload={load} />}
      {custom && <CustomSheet onClose={() => setCustom(false)} onDone={() => { setCustom(false); load() }} />}
    </div>
  )
}

function useEsc(onClose) {
  useEffect(() => {
    const k = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', k)
    return () => window.removeEventListener('keydown', k)
  }, [onClose])
}

// ---------------- sign-in window ----------------
// Opened on the click itself (so the browser doesn't block it), pointed at the app's
// sign-in page once the server hands us the link.
function openSignIn(label) {
  const w = window.open('', 'dot-signin', 'width=520,height=720')
  try {
    const d = w?.document
    if (d) {
      d.title = label
      d.body.style.cssText = 'font-family:system-ui;display:grid;place-items:center;height:90vh;margin:0;background:#fff4e0;color:#1e1b2e'
      d.body.textContent = `Opening ${label}…`
    }
  } catch { /* cross-origin already */ }
  return w
}

export async function connectWith(request, label, needsSignIn) {
  const w = needsSignIn ? openSignIn(label) : null
  try {
    const r = await request()
    if (r.status === 'sign_in' && r.auth_url) {
      if (w && !w.closed) w.location.href = r.auth_url
      else window.open(r.auth_url, '_blank')
    } else {
      w?.close()
    }
    return r
  } catch (e) {
    w?.close()
    throw e
  }
}

const origin = () => location.origin
const isLocal = () => ['localhost', '127.0.0.1', '[::1]'].includes(location.hostname)
const isTunnel = () => location.hostname.endsWith('.trycloudflare.com')

// ---------------- one connected app ----------------
function Installed({ a, app, lang, reload, localRedirect }) {
  const { t } = useTranslation('apps')
  const { agents } = useStore()
  const [more, setMore] = useState(false)
  const [busy, setBusy] = useState(false)
  const label = app ? pick(app.name, lang) : a.label
  const patch = async (body) => {
    try {
      await api(`/api/apps/installed/${a.name}`, { method: 'PATCH', body })
      reload()
    } catch (e) {
      toast('😵 ' + errOf(e).message)
    }
  }
  const [device, setDevice] = useState(null)
  const signIn = async (fresh) => {
    setBusy(true)
    try {
      const builtin = a.kind === 'builtin' && app?.kind !== 'google'  // Google's built-ins sign in on Google's page
      const r = await connectWith(() => api(`/api/apps/installed/${a.name}/connect`, { method: 'POST', body: { origin: origin(), fresh } }), label, !builtin)
      if (r.device) setDevice(r.device)
      reload()
    } catch (e) {
      toast('😵 ' + errOf(e).message)
    }
    setBusy(false)
  }
  const remove = async () => {
    if (!confirm(t('confirmRemove', { name: label }))) return
    await api(`/api/apps/installed/${a.name}`, { method: 'DELETE' })
    toast(t('removed', { name: label }))
    reload()
  }
  const status = a.disabled ? 'off' : a.status
  return (
    <div className={`app-row ${more ? 'open' : ''}`}>
      {device && <DeviceCode app={app || { label }} name={a.name} code={device} lang={lang} onClose={() => { setDevice(null); reload() }} />}
      <div className="row gap10 app-row-top" onClick={() => setMore(!more)}>
        <AppIcon app={app || { label: a.label, color: '#8b8698' }} size={38} />
        <div className="grow min0">
          <div className="row gap6 wrap">
            <b>{label}</b>
            <span className={`app-status s-${status}`}>{status === 'connecting' && <Loader2 size={11} className="spin" />} {t(`status.${status}`)}</span>
          </div>
          <div className="small muted ellipsis">
            {[status === 'error' ? a.error : status === 'connected' ? t('toolsCount', { count: a.tools.length }) : '',
              a.agents === 'all' ? t('who.all') : t('who.some', { count: a.agents.length })].filter(Boolean).join(' · ')}
          </div>
        </div>
        {status === 'sign_in' && <button className="btn sm" disabled={busy} onClick={(e) => { e.stopPropagation(); signIn(false) }}>{busy ? '…' : t('signIn')}</button>}
        {status === 'error' && <button className="btn ghost sm" disabled={busy} onClick={(e) => { e.stopPropagation(); signIn(false) }}>{t('retry')}</button>}
        <ChevronDown size={18} className={`chev ${more ? 'up' : ''}`} />
      </div>
      {status === 'sign_in' && app?.kind === 'google' && (
        <p className="small muted app-hint">{t('google.blocked')} {t('google.mismatch', { addr: origin() + '/oauth/callback', local: localRedirect })}</p>
      )}
      {more && (
        <div className="app-row-more">
          <h5>{t('who.title')}</h5>
          <AgentPicker agents={agents} value={a.agents} onChange={(v) => patch({ agents: v })} />
          {a.tools.length > 0 && (
            <>
              <h5>{t('tools.title')}</h5>
              <p className="small muted">{t('tools.hint')}</p>
              <div className="app-tools">
                {a.tools.map((tl) => (
                  <div key={tl.name} className="app-tool">
                    <div className="grow min0">
                      <div className="small"><b>{tl.title || tl.name}</b> {tl.read_only && <span className="pill tiny">{t('tools.readOnly')}</span>}</div>
                      {tl.description && <div className="small muted ellipsis2">{tl.description}</div>}
                    </div>
                    <div className="seg">
                      {['allow', 'ask', 'never'].map((r) => (
                        <button key={r} className={tl.rule === r ? 'on' : ''} onClick={() => patch({ tool: tl.name, rule: r })}>{t(`tools.${r}`)}</button>
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </>
          )}
          <div className="row gap6 wrap mt8">
            {a.signed_in !== null && <button className="btn ghost sm" onClick={() => signIn(true)}>{t('signInAgain')}</button>}
            <button className="btn ghost sm" onClick={() => patch({ disabled: !a.disabled })}>{a.disabled ? t('turnOn') : t('turnOff')}</button>
            <button className="btn ghost sm danger" onClick={remove}>{t('remove')}</button>
          </div>
        </div>
      )}
    </div>
  )
}

export function AgentPicker({ agents, value, onChange }) {
  const { t } = useTranslation('apps')
  const all = value === 'all'
  const ids = all ? agents.map((a) => a.id) : value || []
  const toggle = (id) => {
    const next = ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]
    onChange(next.length === agents.length && agents.length > 1 ? 'all' : next)
  }
  return (
    <div className="row gap6 wrap agent-pick">
      <button className={`chip ${all ? 'on' : ''}`} onClick={() => onChange(all ? [] : 'all')}>{t('who.everyone')}</button>
      {agents.filter((a) => !a.id.includes('-w')).map((a) => (
        <button key={a.id} className={`chip with-av ${ids.includes(a.id) ? 'on' : ''}`} onClick={() => toggle(a.id)}>
          <Mascot color={a.color} animal={animalFor(a)} size={20} bubble={false} /> {a.name}
        </button>
      ))}
    </div>
  )
}

// ---------------- connect one app from the directory ----------------
function ConnectSheet({ app, d, lang, onClose, onDone, reload, defaultWho }) {
  const { t } = useTranslation('apps')
  useEsc(onClose)
  const { agents } = useStore()
  const front = agents.find((a) => a.origin === 'default') || agents[0]
  const [who, setWho] = useState(defaultWho || (front ? [front.id] : 'all'))
  const [vals, setVals] = useState(() => Object.fromEntries((app.fields || []).map((f) => [f.key, f.default || ''])))
  const [busy, setBusy] = useState(false)
  const [google, setGoogle] = useState(d.google.ready)
  const [ms, setMs] = useState(d.microsoft?.ready)
  const [device, setDevice] = useState(null)
  const name = pick(app.name, lang)
  const missing = (app.fields || []).some((f) => !f.optional && !String(vals[f.key] || '').trim())
  const lacks = app.needs && !d.have[app.needs]
  if (app.kind === 'google' && !google) {
    return <GoogleSetup app={app} setup={d.google} lang={lang} onClose={onClose} onReady={() => { setGoogle(true); reload() }} />
  }
  if (app.kind === 'microsoft' && !ms) {
    return <MicrosoftSetup app={app} setup={d.microsoft} lang={lang} onClose={onClose} onReady={() => { setMs(true); reload() }} />
  }
  if (device) return <DeviceCode app={app} name={device.name} code={device.device} lang={lang} onClose={onDone} />
  const go = async () => {
    setBusy(true)
    try {
      const r = await connectWith(() => api(`/api/apps/connect/${app.id}`, { method: 'POST', body: { values: vals, agents: who, origin: origin() } }),
        name, app.kind === 'oauth' || app.kind === 'google')
      if (r.device) {
        setDevice({ name: r.name, device: r.device })
        setBusy(false)
        return
      }
      toast(r.status === 'connected' ? t('connectedToast', { name }) : r.status === 'sign_in' ? t('signInToast', { name }) : r.status === 'error' ? `😵 ${r.error}` : t('connectingToast', { name }))
      onDone()
    } catch (e) {
      toast('😵 ' + errOf(e).message)
    }
    setBusy(false)
  }
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal app-sheet" onClick={(e) => e.stopPropagation()}>
        <button className="x-btn" onClick={onClose} aria-label="close"><X size={18} /></button>
        <div className="row gap10">
          <AppIcon app={app} size={52} />
          <div>
            <h2>{name}</h2>
            <p className="muted small">{pick(app.blurb, lang)}</p>
          </div>
        </div>
        <div className="app-how">{t(`how.${app.kind}`, { name })}</div>
        {lacks && <p className="small warn-text">{t(`missing.${app.needs}`)}</p>}
        {app.local_only && !['localhost', '127.0.0.1'].includes(location.hostname) && <p className="small warn-text">{t('localOnly', { name })}</p>}
        {(app.fields || []).map((f) => (
          <label key={f.key} className="app-field">
            <span className="small"><b>{pick(f.label, lang)}</b>{f.optional ? ` · ${t('optional')}` : ''}</span>
            {pick(f.help, lang) && <span className="small muted">{pick(f.help, lang)}</span>}
            <div className="row gap6">
              <input className="input" type={f.secret ? 'password' : 'text'} autoComplete={f.secret ? 'new-password' : 'off'}
                placeholder={f.placeholder} value={vals[f.key]} onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })} />
              {f.link && <a className="btn ghost sm" href={f.link} target="_blank" rel="noreferrer">{t('getIt')} <ExternalLink size={12} /></a>}
            </div>
          </label>
        ))}
        <h5>{t('who.title')}</h5>
        <AgentPicker agents={agents} value={who} onChange={setWho} />
        <p className="small muted">{t('safety')}</p>
        <div className="row gap6 end">
          {app.docs && <a className="btn ghost sm" href={app.docs} target="_blank" rel="noreferrer">{t('about')} <ExternalLink size={12} /></a>}
          <button className="btn" disabled={busy || missing || lacks || (who !== 'all' && !who.length)} onClick={go}>
            {busy ? <Loader2 size={15} className="spin" /> : null} {['oauth', 'google', 'microsoft'].includes(app.kind) ? t('connectSignIn') : t('connect')}
          </button>
        </div>
      </div>
    </div>
  )
}

// ---------------- Google: your own sign-in client, once ----------------
function GoogleSetup({ app, setup, lang, onClose, onReady }) {
  const { t } = useTranslation('apps')
  useEsc(onClose)
  const [cid, setCid] = useState('')
  const [secret, setSecret] = useState('')
  const [busy, setBusy] = useState(false)
  const here = origin() + setup.redirect_path
  // the localhost address never changes; a tunnel link does on every restart
  const addrs = isLocal() ? [[here, '']] : [[setup.local_redirect, t('google.fixed')], [here, isTunnel() ? t('google.temporary') : t('google.thisLink')]]
  const apis = [['calendar-json.googleapis.com', 'Google Calendar API'], ['gmail.googleapis.com', 'Gmail API'],
    ['drive.googleapis.com', 'Google Drive API'], ['mapstools.googleapis.com', 'Google Maps']]
  const save = async () => {
    setBusy(true)
    try {
      await api('/api/apps/google', { method: 'PUT', body: { client_id: cid.trim(), client_secret: secret.trim() } })
      toast(t('google.saved'))
      onReady()
    } catch (e) {
      toast('😵 ' + errOf(e).message)
    }
    setBusy(false)
  }
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal app-sheet wide" onClick={(e) => e.stopPropagation()}>
        <button className="x-btn" onClick={onClose} aria-label="close"><X size={18} /></button>
        <div className="row gap10">
          <AppIcon app={app} size={44} />
          <div>
            <h2>{t('google.title')}</h2>
            <p className="muted small">{t('google.lead')}</p>
          </div>
        </div>
        {!isLocal() && (
          <p className="small warn-text">{t(location.protocol !== 'https:' ? 'google.https' : isTunnel() ? 'google.tunnel' : 'google.remote', { local: setup.local_redirect.replace(setup.redirect_path, '') })}</p>
        )}
        <ol className="g-steps">
          {setup.steps.map((s, i) => (
            <li key={i}>
              {pick(s, lang)}
              {i === 0 && <a className="btn ghost sm" href={setup.console} target="_blank" rel="noreferrer">Google Cloud <ExternalLink size={12} /></a>}
              {i === 1 && (
                <div className="row gap6 wrap mt4">
                  {apis.map(([id, n]) => (
                    <a key={id} className="chip" href={`https://console.cloud.google.com/apis/library/${id}`} target="_blank" rel="noreferrer">{n} <ExternalLink size={11} /></a>
                  ))}
                </div>
              )}
              {i === 3 && (
                <>
                  {addrs.length > 1 && <div className="small muted mt4">{t('google.bothAddrs')}</div>}
                  {addrs.map(([addr, note]) => (
                    <div key={addr} className="copy-line">
                      <code>{addr}</code>
                      {note && <span className="small muted">{note}</span>}
                      <button className="btn ghost sm" onClick={() => { navigator.clipboard?.writeText(addr); toast(t('copied')) }}><Copy size={13} /></button>
                    </div>
                  ))}
                </>
              )}
            </li>
          ))}
        </ol>
        <label className="app-field"><span className="small"><b>{t('google.clientId')}</b></span>
          <input className="input mono" placeholder="…apps.googleusercontent.com" value={cid} onChange={(e) => setCid(e.target.value)} /></label>
        <label className="app-field"><span className="small"><b>{t('google.clientSecret')}</b></span>
          <input className="input mono" type="password" autoComplete="new-password" value={secret} onChange={(e) => setSecret(e.target.value)} /></label>
        <p className="small muted">{t('google.privacy')}</p>
        <div className="row end">
          <button className="btn" disabled={busy || !cid.trim() || !secret.trim()} onClick={save}>{t('google.save')}</button>
        </div>
      </div>
    </div>
  )
}

// ---------------- anything else: a link or a program ----------------
function CustomSheet({ onClose, onDone }) {
  const { t } = useTranslation('apps')
  useEsc(onClose)
  const { agents } = useStore()
  const front = agents.find((a) => a.origin === 'default') || agents[0]
  const [mode, setMode] = useState('url')
  const [v, setV] = useState({ label: '', url: '', token: '', command: '' })
  const [who, setWho] = useState(front ? [front.id] : 'all')
  const [busy, setBusy] = useState(false)
  const go = async () => {
    setBusy(true)
    try {
      const body = { label: v.label, agents: who, origin: origin(), ...(mode === 'url' ? { url: v.url, token: v.token } : { command: v.command }) }
      const r = await connectWith(() => api('/api/apps/custom', { method: 'POST', body }), v.label || 'app', mode === 'url' && !v.token)
      toast(r.status === 'connected' ? t('connectedToast', { name: r.label }) : r.status === 'error' ? `😵 ${r.error}` : t('connectingToast', { name: r.label }))
      onDone()
    } catch (e) {
      toast('😵 ' + errOf(e).message)
    }
    setBusy(false)
  }
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal app-sheet" onClick={(e) => e.stopPropagation()}>
        <button className="x-btn" onClick={onClose} aria-label="close"><X size={18} /></button>
        <h2>{t('custom.title')}</h2>
        <p className="muted small">{t('custom.lead')}</p>
        <div className="seg wide">
          <button className={mode === 'url' ? 'on' : ''} onClick={() => setMode('url')}>{t('custom.link')}</button>
          <button className={mode === 'cmd' ? 'on' : ''} onClick={() => setMode('cmd')}>{t('custom.program')}</button>
        </div>
        <label className="app-field"><span className="small"><b>{t('custom.name')}</b></span>
          <input className="input" value={v.label} onChange={(e) => setV({ ...v, label: e.target.value })} /></label>
        {mode === 'url' ? (
          <>
            <label className="app-field"><span className="small"><b>{t('custom.url')}</b></span>
              <input className="input mono" placeholder="https://…/mcp" value={v.url} onChange={(e) => setV({ ...v, url: e.target.value })} /></label>
            <label className="app-field"><span className="small"><b>{t('custom.token')}</b> · {t('optional')}</span>
              <span className="small muted">{t('custom.tokenHint')}</span>
              <input className="input mono" type="password" autoComplete="new-password" value={v.token} onChange={(e) => setV({ ...v, token: e.target.value })} /></label>
          </>
        ) : (
          <label className="app-field"><span className="small"><b>{t('custom.command')}</b></span>
            <input className="input mono" placeholder="npx -y @scope/some-mcp-server" value={v.command} onChange={(e) => setV({ ...v, command: e.target.value })} /></label>
        )}
        <h5>{t('who.title')}</h5>
        <AgentPicker agents={agents} value={who} onChange={setWho} />
        <p className="small muted">{t('custom.caution')}</p>
        <div className="row end">
          <button className="btn" disabled={busy || (mode === 'url' ? !v.url.trim() : !v.command.trim())} onClick={go}>{busy ? '…' : t('connect')}</button>
        </div>
      </div>
    </div>
  )
}

// ---------------- in chat: an agent suggests connecting an app ----------------
export function AppOffer({ card, agent }) {
  const { t, i18n } = useTranslation('apps')
  const lang = i18n.language
  const [d, setD] = useState(null)
  const [open, setOpen] = useState(false)
  const load = () => api('/api/apps').then(setD).catch(() => {})
  useEffect(() => { load() }, [])
  const app = d?.apps.find((a) => a.id === card.app)
  if (!app) return null
  const got = d.installed.find((x) => x.app === app.id && x.status === 'connected')
  const name = pick(app.name, lang)
  return (
    <>
      <div className="app-offer">
        <AppIcon app={app} size={44} />
        <div className="grow min0">
          <b>{t('card.title', { name })}</b>
          <p className="small muted">{card.why || pick(app.blurb, lang)}</p>
        </div>
        {got ? <span className="pill ok">{t('card.done')}</span>
          : <button className="btn sm" onClick={() => setOpen(true)}>{t('card.button')}</button>}
      </div>
      {open && <ConnectSheet app={app} d={d} lang={lang} defaultWho={agent ? [agent.id] : undefined}
        onClose={() => setOpen(false)} onDone={() => { setOpen(false); load() }} reload={load} />}
    </>
  )
}

// ---------------- Microsoft: a client ID of your own, once ----------------
function MicrosoftSetup({ app, setup, lang, onClose, onReady }) {
  const { t } = useTranslation('apps')
  useEsc(onClose)
  const [cid, setCid] = useState('')
  const [busy, setBusy] = useState(false)
  const save = async () => {
    setBusy(true)
    try {
      await api('/api/apps/microsoft', { method: 'PUT', body: { client_id: cid.trim() } })
      toast(t('microsoft.saved'))
      onReady()
    } catch (e) {
      toast('😵 ' + errOf(e).message)
    }
    setBusy(false)
  }
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal app-sheet wide" onClick={(e) => e.stopPropagation()}>
        <button className="x-btn" onClick={onClose} aria-label="close"><X size={18} /></button>
        <div className="row gap10">
          <AppIcon app={app} size={44} />
          <div>
            <h2>{t('microsoft.title')}</h2>
            <p className="muted small">{t('microsoft.lead')}</p>
          </div>
        </div>
        <ol className="g-steps">
          {setup.steps.map((s, i) => (
            <li key={i}>
              {pick(s, lang)}
              {i === 0 && <a className="btn ghost sm" href={setup.portal} target="_blank" rel="noreferrer">Microsoft Entra <ExternalLink size={12} /></a>}
            </li>
          ))}
        </ol>
        <label className="app-field"><span className="small"><b>{t('microsoft.clientId')}</b></span>
          <input className="input mono" placeholder="00000000-0000-0000-0000-000000000000" value={cid} onChange={(e) => setCid(e.target.value)} /></label>
        <p className="small muted">{t('microsoft.privacy')}</p>
        <div className="row end">
          <button className="btn" disabled={busy || !cid.trim()} onClick={save}>{t('google.save')}</button>
        </div>
      </div>
    </div>
  )
}

// ---------------- sign in with a short code (Microsoft) ----------------
function DeviceCode({ app, name, code, lang, onClose }) {
  const { t } = useTranslation('apps')
  useEsc(onClose)
  const [done, setDone] = useState(false)
  useEffect(() => {
    const id = setInterval(async () => {
      try {
        const d = await api('/api/apps')
        const me = d.installed.find((x) => x.name === name)
        if (me?.status === 'connected') {
          setDone(true)
          clearInterval(id)
          setTimeout(onClose, 1200)
        }
      } catch { /* keep waiting */ }
    }, 2500)
    return () => clearInterval(id)
  }, [name])
  const label = app?.name ? pick(app.name, lang) : app?.label
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal app-sheet device" onClick={(e) => e.stopPropagation()}>
        <button className="x-btn" onClick={onClose} aria-label="close"><X size={18} /></button>
        <div className="row gap10">
          <AppIcon app={app} size={44} />
          <h2>{done ? t('connectedToast', { name: label }) : t('device.title', { name: label })}</h2>
        </div>
        {!done && (
          <>
            <p className="small">{t('device.step1')}</p>
            <a className="btn" href={code.verification_uri} target="_blank" rel="noreferrer">{code.verification_uri.replace('https://', '')} <ExternalLink size={13} /></a>
            <p className="small">{t('device.step2')}</p>
            <div className="device-code">
              <code>{code.user_code}</code>
              <button className="btn ghost sm" onClick={() => { navigator.clipboard?.writeText(code.user_code); toast(t('copied')) }}><Copy size={13} /></button>
            </div>
            <p className="small muted row gap6"><Loader2 size={13} className="spin" /> {t('device.waiting')}</p>
          </>
        )}
      </div>
    </div>
  )
}
