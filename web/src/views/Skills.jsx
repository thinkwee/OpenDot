import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { Trans, useTranslation } from 'react-i18next'
import { Download, ExternalLink, Search, Star, X } from 'lucide-react'
import { api, toast, useStore } from '../store'
import { md } from '../util'
import './Skills.css'

const ICONS = {
  'deep-research': '🔎', 'morning-brief': '☀️', 'trip-planner': '🧳',
  'inbox-triage': '📮', 'weekly-review': '🗒️', 'make-a-page': '✨',
  'price-watch': '🏷️', 'meeting-prep': '🗓️',
}
const iconFor = (name) => ICONS[name] || '📚'

// fixedAgent set → embedded in that agent's profile: just its on/off switches, no installer
export default function Skills({ agentId: fixedAgent }) {
  const { t } = useTranslation('skills')
  const { agents } = useStore()
  const [skills, setSkills] = useState(null)
  const [agentId, setAgentId] = useState(fixedAgent || '')
  const [url, setUrl] = useState('')
  const [installing, setInstalling] = useState(false)
  const [installResult, setInstallResult] = useState(null)
  const [open, setOpen] = useState(null) // skill name being viewed

  const load = (aid) =>
    api(`/api/skills${aid ? `?agent_id=${aid}` : ''}`).then(setSkills)

  useEffect(() => {
    if (agents.length && !agentId) setAgentId(agents[0].id)
  }, [agents])
  useEffect(() => {
    if (agentId) load(agentId)
  }, [agentId])

  const toggle = async (name, on) => {
    try {
      await api(`/api/skills/${name}/agents/${agentId}`, { method: 'PATCH', body: { enabled: on } })
      load(agentId)
    } catch (e) {
      toast('⚠️ ' + e.message)
    }
  }

  const install = async (spec) => {
    const target = (typeof spec === 'string' ? spec : url).trim()
    if (!target) return
    setInstalling(target)
    setInstallResult(null)
    try {
      const r = await api('/api/skills/install', { method: 'POST', body: { url: target } })
      setInstallResult(r.installed)
      const flagged = r.installed.some((s) => (s.findings || []).length)
      toast(flagged ? t('install.flagged') : t('install.done'))
      setUrl('')
      load(agentId)
    } catch (e) {
      toast('⚠️ ' + e.message)
    } finally {
      setInstalling(false)
    }
  }

  const confirmSkill = async (name) => {
    await api(`/api/skills/${name}/confirm`, { method: 'POST' })
    load(agentId)
  }

  const remove = async (name) => {
    if (!window.confirm(t('deleteAsk', { name }))) return
    await api(`/api/skills/${name}`, { method: 'DELETE' })
    load(agentId)
  }

  if (!skills) return null
  return (
    <div>
      <header className="page-head" style={fixedAgent ? { display: 'none' } : null}>
        <div>
          <h1>{t('title')}</h1>
          <p className="muted">{t('intro')}</p>
        </div>
      </header>

      {!fixedAgent && <Discover onInstall={install} installing={installing} />}

      <section className="section card" style={fixedAgent ? { display: 'none' } : null}>
        <h3>{t('install.title')}</h3>
        <p className="muted small">{t('install.body')}</p>
        <div className="row gap6">
          <input className="input" placeholder="https://github.com/someone/their-skills" value={url} onChange={(e) => setUrl(e.target.value)} />
          <button className="btn" disabled={!url.trim() || !!installing} onClick={() => install()}>{installing === url.trim() ? t('install.installing') : t('install.button')}</button>
        </div>
        {installResult && (
          <div className="skill-scan-results mt8">
            {installResult.map((r) => (
              <div key={r.name} className={`skill-scan-row ${r.findings?.length ? 'flagged' : 'clean'}`}>
                <b>{r.name}</b>
                {r.error && <span className="muted small"> — {r.error}</span>}
                {!r.error && !r.findings?.length && <span className="pill ok">{t('install.clean')}</span>}
                {!!r.findings?.length && (
                  <div className="mt6">
                    <span className="pill bad">{t('install.flaggedCount', { count: r.findings.length })}</span>
                    <ul className="skill-findings">
                      {r.findings.map((f, i) => (
                        <li key={i}><b>{f.file}</b>: {f.why} <code className="code sm">{f.snippet}</code></li>
                      ))}
                    </ul>
                    <button className="btn sm" onClick={() => confirmSkill(r.name)}>{t('install.allow')}</button>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </section>

      {!fixedAgent && agents.length > 1 && (
        <div className="seg skill-agent-seg">
          {agents.map((a) => (
            <button key={a.id} className={agentId === a.id ? 'on' : ''} onClick={() => setAgentId(a.id)}>
              {a.emoji} {a.name}
            </button>
          ))}
        </div>
      )}

      <div className="skills-grid">
        {skills.map((s) => (
          <div key={s.name} className={`card skill-card ${s.enabled ? '' : 'off'}`}>
            <div className="row between">
              <div className="row gap8">
                <div className="skill-emoji">{iconFor(s.name)}</div>
                <div>
                  <b>{s.name}</b>
                  <div className="small muted">{s.root === 'bundled' ? t('source.builtin') : s.source?.startsWith('agent:') ? t('source.savedBy', { name: s.source.slice(6) }) : t('source.installed')}</div>
                </div>
              </div>
              <label className="switch">
                <input type="checkbox" checked={!!s.enabled}
                  disabled={!!s.scan?.length && !s.confirmed}
                  onChange={(e) => toggle(s.name, e.target.checked)} />
                <span />
              </label>
            </div>
            <p className="small mt8">{s.description}</p>
            <div className="row gap6 wrap mt8">
              {!!s.scan?.length && !s.confirmed && <span className="pill bad">{t('needsReview')}</span>}
              {!!s.scan?.length && s.confirmed && <span className="pill">{t('reviewed')}</span>}
              {s.files?.length > 0 && <span className="pill">{t('files', { count: s.files.length })}</span>}
            </div>
            <div className="row gap6 mt8">
              <button className="btn ghost sm" onClick={() => setOpen(s.name)}>{t('read')}</button>
              {s.root === 'user' && <button className="btn ghost sm" onClick={() => remove(s.name)}>{t('common:delete')}</button>}
            </div>
          </div>
        ))}
        {skills.length === 0 && <div className="empty-card"><p className="muted">{t('empty')}</p></div>}
      </div>

      {open && <SkillModal name={open} onClose={() => setOpen(null)} />}
    </div>
  )
}

function SkillModal({ name, onClose }) {
  const { t } = useTranslation('skills')
  const [data, setData] = useState(null)
  useEffect(() => { api(`/api/skills/${name}`).then(setData) }, [name])
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="row between">
          <h3>{iconFor(name)} {name}</h3>
          <button className="btn ghost sm" onClick={onClose}>✕</button>
        </div>
        {!data && <p className="muted">{t('common:loading')}</p>}
        {data && (
          <div className="skill-md" dangerouslySetInnerHTML={{ __html: md(data.instructions) }} />
        )}
      </div>
    </div>
  )
}

// Search the open skill ecosystem (skills.sh + SkillsMP) → preview → install
function Discover({ onInstall, installing }) {
  const { t } = useTranslation('skills')
  const [q, setQ] = useState('')
  const [res, setRes] = useState(null)
  const [busy, setBusy] = useState(false)
  const [peek, setPeek] = useState(null) // {item, text}
  useEffect(() => {
    const term = q.trim()
    if (term.length < 2) { setRes(null); return }
    setBusy(true)
    const h = setTimeout(() => {
      api(`/api/skillhub/search?q=${encodeURIComponent(term)}&limit=18`)
        .then((r) => setRes(r.results))
        .catch((e) => { toast('⚠️ ' + e.message.slice(0, 120)); setRes([]) })
        .finally(() => setBusy(false))
    }, 350)
    return () => clearTimeout(h)
  }, [q])
  const preview = async (item) => {
    setPeek({ item, text: null })
    try {
      const r = await api('/api/skillhub/preview', { method: 'POST', body: item })
      setPeek({ item, text: r.text.replace(/^---[\s\S]*?---\s*/, '') })
    } catch (e) {
      setPeek({ item, text: `_${e.message}_` })
    }
  }
  const fmt = (n) => (n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : n)
  return (
    <section className="section card discover">
      <h3>{t('discover.title')}</h3>
      <p className="muted small"><Trans t={t} i18nKey="discover.body" components={{ a1: <a href="https://skills.sh" target="_blank" rel="noopener" />, a2: <a href="https://skillsmp.com" target="_blank" rel="noopener" /> }} /></p>
      <div className="discover-search">
        <Search size={18} />
        <input className="input" placeholder={t('discover.placeholder')} value={q} onChange={(e) => setQ(e.target.value)} />
        {busy && <span className="fv-spin" />}
      </div>
      {!res && (
        <div className="row gap6 wrap mt8">
          {['pdf', 'excel', 'slides', 'notion', 'travel', 'email', 'research', 'design'].map((k) => (
            <button key={k} className="chip" onClick={() => setQ(k)}>{t(`discover.chips.${k}`)}</button>
          ))}
        </div>
      )}
      {res && res.length === 0 && !busy && <p className="muted small mt8">{t('discover.nothing')}</p>}
      {res && res.length > 0 && (
        <div className="discover-grid">
          {res.map((s) => (
            <div key={s.source + s.name} className="hub-card">
              <div className="row between gap6">
                <b className="ellipsis">{s.name}</b>
                <span className="row gap6">
                  {s.official && <span className="pill ok">{t('discover.official')}</span>}
                  {s.installed && <span className="pill">{t('discover.installed')}</span>}
                </span>
              </div>
              <small className="muted ellipsis">{s.source}</small>
              {s.description && <p className="small hub-desc">{s.description}</p>}
              <div className="row between mt8">
                <span className="hub-meta">
                  {s.installs ? <span title={t('discover.installs')}><Download size={12} /> {fmt(s.installs)}</span> : null}
                  {s.stars ? <span title={t('discover.stars')}><Star size={12} /> {fmt(s.stars)}</span> : null}
                </span>
                <span className="row gap6">
                  <button className="btn ghost sm" onClick={() => preview(s)}>{t('discover.preview')}</button>
                  <button className="btn sm" disabled={!!installing || s.installed} onClick={() => onInstall(s.install)}>
                    {installing === s.install ? t('install.installing') : s.installed ? t('install.added') : t('install.button')}
                  </button>
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
      {peek && createPortal(
        <div className="modal-bg" onClick={() => setPeek(null)}>
          <div className="modal skill-peek" onClick={(e) => e.stopPropagation()}>
            <div className="row between">
              <h2 className="ellipsis">{peek.item.name}</h2>
              <button className="icon-btn" onClick={() => setPeek(null)}><X size={20} /></button>
            </div>
            <small className="muted">{t('discover.from', { source: peek.item.source, from: peek.item.from })}</small>
            <div className="skill-peek-body md">{peek.text == null ? <p className="muted">{t('common:loading')}</p> : <div dangerouslySetInnerHTML={{ __html: md(peek.text) }} />}</div>
            <div className="row gap6 end">
              <a className="btn ghost sm" href={peek.item.url} target="_blank" rel="noopener noreferrer"><ExternalLink size={14} /> {t('discover.sourceLink')}</a>
              <button className="btn sm" disabled={!!installing || peek.item.installed} onClick={() => { onInstall(peek.item.install); setPeek(null) }}>{t('install.button')}</button>
            </div>
          </div>
        </div>, document.body)}
    </section>
  )
}
