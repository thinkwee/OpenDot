import { useEffect, useState } from 'react'
import { useTranslation, Trans } from 'react-i18next'
import { api, toast, useStore } from '../store'
import { errOf, pick } from './idWizard'
import './Connectors.css'

export default function Connectors() {
  const { t } = useTranslation('connectors')
  const { hookToken } = useStore()
  const [cfg, setCfg] = useState(null)
  const [raw, setRaw] = useState('')
  const [status, setStatus] = useState({})
  const [tools, setTools] = useState([])
  const [vault, setVault] = useState([])
  const [sec, setSec] = useState({ name: '', value: '' })
  const [feed, setFeed] = useState({ name: '', url: '' })

  const load = () =>
    api('/api/connectors').then((r) => {
      setCfg(r.config)
      setRaw(JSON.stringify(r.config.mcp || {}, null, 2))
      setStatus(r.status)
      setTools(r.tools)
    })
  useEffect(() => {
    load()
    api('/api/vault').then(setVault)
  }, [])

  const saveMcp = async () => {
    try {
      const mcp = JSON.parse(raw || '{}')
      await api('/api/connectors', { method: 'PUT', body: { ...cfg, mcp } })
      toast(`🔌 ${t('connecting')}`)
      setTimeout(load, 4000)
    } catch (e) {
      toast(t('badJson', { msg: e.message }))
    }
  }
  const addFeed = async () => {
    const rss = { ...(cfg.rss || {}), [feed.name]: feed.url }
    await api('/api/connectors', { method: 'PUT', body: { ...cfg, rss } })
    setFeed({ name: '', url: '' })
    load()
  }
  const delFeed = async (k) => {
    const rss = { ...(cfg.rss || {}) }
    delete rss[k]
    await api('/api/connectors', { method: 'PUT', body: { ...cfg, rss } })
    load()
  }
  const addSecret = async () => {
    setVault(await api('/api/vault', { method: 'POST', body: sec }))
    setSec({ name: '', value: '' })
  }

  if (!cfg) return null
  const hook = `${location.origin}/hook/${hookToken}`
  return (
    <div>
      <header className="page-head">
        <div>
          <h1>{t('title')}</h1>
          <p className="muted">{t('lead')}</p>
        </div>
      </header>

      <Catalog onAdded={() => setTimeout(load, 4000)} />

      <section className="section card">
        <h3>🧩 {t('mcp.title')}</h3>
        <p className="muted small">{t('mcp.hintBefore')} <code>{'{{vault:NAME}}'}</code>{t('mcp.hintAfter')}</p>
        <textarea className="input mono" rows={8} value={raw} onChange={(e) => setRaw(e.target.value)} />
        <div className="row between mt8 wrap gap6">
          <div className="row gap6 wrap">
            {Object.entries(status).map(([k, v]) => (
              <span key={k} className={`pill ${v === 'connected' ? 'ok' : 'bad'}`}>{k}: {v}</span>
            ))}
            {tools.length > 0 && <span className="pill">{t('tools', { count: tools.length })}</span>}
          </div>
          <button className="btn" onClick={saveMcp}>{t('saveConnect')}</button>
        </div>
      </section>

      <section className="section card">
        <h3>📰 {t('feeds.title')}</h3>
        <p className="muted small"><Trans t={t} i18nKey="feeds.hint" components={{ code: <code /> }} /></p>
        {Object.entries(cfg.rss || {}).map(([k, v]) => (
          <div key={k} className="row between feed">
            <span><b>{k}</b> <small className="muted">{v}</small></span>
            <button className="btn ghost sm" onClick={() => delFeed(k)}>✕</button>
          </div>
        ))}
        <div className="row gap6 mt8">
          <input className="input" placeholder={t('feeds.name')} value={feed.name} onChange={(e) => setFeed({ ...feed, name: e.target.value })} style={{ maxWidth: 140 }} />
          <input className="input" placeholder="https://…/rss" value={feed.url} onChange={(e) => setFeed({ ...feed, url: e.target.value })} />
          <button className="btn" disabled={!feed.name || !feed.url} onClick={addFeed}>{t('feeds.add')}</button>
        </div>
      </section>

      <section className="section card">
        <h3>📲 {t('share.title')}</h3>
        <p className="muted small">{t('share.hint')}</p>
        <pre className="code">{`POST ${hook}/share      {"text": "…", "url": "…"}\nPOST ${hook}/<source>   any JSON → event webhook:<source>`}</pre>
      </section>

      <section className="section card">
        <h3>🔐 {t('vault.title')}</h3>
        <p className="muted small">{t('vault.hint')}</p>
        <div className="row gap6 wrap mb8">
          {vault.map((n) => (
            <span key={n} className="pill">
              {n}{' '}
              <button className="x" onClick={async () => setVault(await api('/api/vault', { method: 'POST', body: { name: n, value: null } }))}>✕</button>
            </span>
          ))}
          {vault.length === 0 && <small className="muted">{t('vault.empty')}</small>}
        </div>
        <div className="row gap6">
          <input className="input mono" placeholder="NAME" value={sec.name} onChange={(e) => setSec({ ...sec, name: e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, '_') })} style={{ maxWidth: 180 }} />
          <input className="input" type="password" placeholder={t('vault.valuePh')} value={sec.value} onChange={(e) => setSec({ ...sec, value: e.target.value })} />
          <button className="btn" disabled={!sec.name || !sec.value} onClick={addSecret}>{t('common:save')}</button>
        </div>
      </section>
    </div>
  )
}

// ---------------- app catalog: pick an app → paste its token → Add ----------------
function Catalog({ onAdded }) {
  const { t, i18n } = useTranslation('connectors')
  const lang = i18n.language
  const [cat, setCat] = useState(null)
  const [filter, setFilter] = useState('all')
  const [open, setOpen] = useState('')
  const load = () => api('/api/connectors/catalog').then(setCat).catch(() => setCat(null))
  useEffect(() => { load() }, [])
  if (!cat) return null
  const entries = cat.entries.filter((e) => filter === 'all' || e.category === filter)
  const added = (e) => cat.installed.some((n) => n === e.id || n.startsWith(`${e.id}-`))
  return (
    <section className="section card">
      <h3>✨ {t('catalog.title')}</h3>
      <p className="muted small">{t('catalog.lead')}</p>
      <div className="row gap6 wrap mb8">
        <button className={`chip ${filter === 'all' ? 'on' : ''}`} onClick={() => setFilter('all')}>{t('catalog.all')}</button>
        {cat.categories.map((c) => (
          <button key={c.id} className={`chip ${filter === c.id ? 'on' : ''}`} onClick={() => setFilter(c.id)}>{pick(c.label, lang)}</button>
        ))}
      </div>
      <div className="cat-grid">
        {entries.map((e) => (
          <div key={e.id} className={`cat-card ${open === e.id ? 'on' : ''}`}>
            <div className="row gap8 cat-top">
              <span className="cat-emoji">{e.emoji}</span>
              <div className="grow">
                <b>{pick(e.name, lang)}</b>
                <div className="row gap6 wrap">
                  {e.needs && <span className={`pill ${cat.have[e.needs] ? '' : 'bad'}`} title={cat.have[e.needs] ? '' : t(`catalog.missing.${e.needs}`)}>{t(`catalog.needs.${e.needs}`)}{cat.have[e.needs] ? '' : ' ⚠'}</span>}
                  {!e.needs && <span className="pill">{t('catalog.nothingToInstall')}</span>}
                  {added(e) && <span className="pill ok">{t('catalog.added')}</span>}
                </div>
              </div>
            </div>
            <p className="small muted cat-blurb">{pick(e.blurb, lang)}</p>
            {open === e.id
              ? <AddForm entry={e} have={cat.have} lang={lang} onCancel={() => setOpen('')}
                  onDone={(name) => { setOpen(''); load(); onAdded(name) }} />
              : <button className="btn sm" onClick={() => setOpen(e.id)}>＋ {added(e) ? t('catalog.addAnother') : t('catalog.add')}</button>}
          </div>
        ))}
      </div>
    </section>
  )
}

function AddForm({ entry, have, lang, onCancel, onDone }) {
  const { t } = useTranslation('connectors')
  const [vals, setVals] = useState(() => Object.fromEntries(entry.fields.map((f) => [f.key, f.default || ''])))
  const [busy, setBusy] = useState(false)
  const missing = entry.fields.some((f) => !f.optional && !String(vals[f.key] || '').trim())
  const add = async () => {
    setBusy(true)
    try {
      const r = await api(`/api/connectors/catalog/${entry.id}`, { method: 'POST', body: { values: vals } })
      toast(t('catalog.addedToast', { name: pick(entry.name, lang) }))
      onDone(r.name)
    } catch (e) {
      toast('❌ ' + errOf(e).message)
    }
    setBusy(false)
  }
  return (
    <div className="cat-form">
      {entry.needs && !have[entry.needs] && <p className="small cat-warn">{t(`catalog.missing.${entry.needs}`)}</p>}
      {entry.fields.map((f) => (
        <label key={f.key} className="cat-field">
          <span className="small"><b>{pick(f.label, lang)}</b>{f.optional ? ` · ${t('catalog.optional')}` : ''}</span>
          {pick(f.help, lang) && <span className="small muted">{pick(f.help, lang)}</span>}
          <div className="row gap6">
            <input className="input" type={f.secret ? 'password' : 'text'} autoComplete={f.secret ? 'new-password' : 'off'} placeholder={f.placeholder}
              value={vals[f.key]} onChange={(e) => setVals({ ...vals, [f.key]: e.target.value })} />
            {f.link && <a className="btn ghost sm" href={f.link} target="_blank" rel="noreferrer">{t('catalog.getIt')} ↗</a>}
          </div>
        </label>
      ))}
      {entry.fields.length === 0 && <p className="small muted">{t('catalog.noSetup')}</p>}
      {entry.note && <p className="small cat-warn">{pick(entry.note, lang)}</p>}
      <p className="small muted">{entry.fields.some((f) => f.secret) ? t('catalog.vaultNote') : ''} {entry.source && <a href={entry.source} target="_blank" rel="noreferrer">{t('catalog.source')} ↗</a>}</p>
      <div className="row gap6">
        <button className="btn" disabled={busy || missing} onClick={add}>{busy ? '…' : t('catalog.addConnect')}</button>
        <button className="btn ghost" onClick={onCancel}>{t('common:cancel')}</button>
      </div>
    </div>
  )
}
