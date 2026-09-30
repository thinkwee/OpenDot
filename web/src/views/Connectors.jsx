import { useEffect, useState } from 'react'
import { useTranslation, Trans } from 'react-i18next'
import { api, toast, useStore } from '../store'
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
