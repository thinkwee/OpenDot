import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Eye, Mail, Monitor, Pause, Phone, Play, Plug, Timer, Trash2 } from 'lucide-react'
import { api, store, toast, useStore } from '../store'
import AppIcon from '../components/AppIcon'
import Mascot, { ANIMALS, animalFor } from '../components/Mascot'
import { go } from '../App'
import Memory from './Memory'
import Skills from './Skills'
import { AgentIdentity } from './Identity'
import Boundary from '../components/Boundary'
import { COLORS, ago } from '../util'
import './Team.css'
import './soul.css'

// #/team/<agentId>[/<tab>] → that agent's card. (#/team alone → home.)
export default function Team({ agentId, sub }) {
  const { agents } = useStore()
  const agent = agents.find((a) => a.id === agentId)
  useEffect(() => {
    if (!agent) go('chats')
  }, [agent])
  return agent ? <AgentCard key={agent.id} agent={agent} tab={sub} /> : null
}

const MORE = ['memory', 'skills', 'identity', 'checkins']

function AgentCard({ agent, tab }) {
  const { t } = useTranslation('agent')
  const { threads, hookToken, running } = useStore()
  const dm = threads.find((t) => t.kind === 'dm' && t.members[0] === agent.id)
  const [addr, setAddr] = useState(null)
  const [phone, setPhone] = useState(null)
  const [routines, setRoutines] = useState([])
  const [apps, setApps] = useState(null)
  const [faces, setFaces] = useState(false)
  const busy = running[agent.id]

  const loadRoutines = () => api('/api/automations').then((l) => setRoutines(l.filter((r) => r.agent_id === agent.id))).catch(() => {})
  useEffect(() => {
    api('/api/identity/addresses').then((m) => setAddr(m[agent.id] || '')).catch(() => setAddr(''))
    api(`/api/identity/phone/${agent.id}`).then((c) => setPhone(c.from_number || '')).catch(() => setPhone(''))
    api('/api/apps').then((d) => setApps(d.installed.filter((a) => !a.disabled && (a.agents === 'all' || (a.agents || []).includes(agent.id))).length)).catch(() => setApps(0))
    loadRoutines()
    const on = () => loadRoutines()
    window.addEventListener('dot:automation', on)
    return () => window.removeEventListener('dot:automation', on)
  }, [agent.id])

  const patch = async (body) => {
    const a = await api(`/api/agents/${agent.id}`, { method: 'PATCH', body })
    store.set((s) => ({ agents: s.agents.map((x) => (x.id === a.id ? { ...x, ...a } : x)) }))
  }
  const letGo = async () => {
    if (!confirm(t('letGo.ask', { name: agent.name }))) return
    try {
      await api(`/api/agents/${agent.id}`, { method: 'DELETE' })
      toast(t('letGo.bye', { name: agent.name }))
      go('chats')
    } catch (e) {
      toast(e.message.includes('at least one') ? t('letGo.keepOne') : e.message)
    }
  }
  const toggle = async (r) => {
    await api(`/api/automations/${r.id}`, { method: 'PATCH', body: { enabled: r.enabled ? 0 : 1 } })
    loadRoutines()
  }
  const every = (m) => {
    m = m || 60
    if (m % 1440 === 0) return m === 1440 ? t('watching.every.day') : t('watching.every.days', { n: m / 1440 })
    if (m % 60 === 0) return m === 60 ? t('watching.every.hourly') : t('watching.every.hour', { n: m / 60 })
    return t('watching.every.min', { n: m })
  }
  const watches = routines.filter((r) => r.kind === 'watch')
  const regular = routines.filter((r) => r.kind !== 'watch')

  return (
    <div className="profile">
      <button className="back-link" onClick={() => (dm ? go('chats', dm.id) : go('chats'))}>{t('backToChat')}</button>

      <section className="idc" style={{ '--c': agent.color }}>
        <div className="idc-top">
          <button className="idc-face" onClick={() => setFaces(!faces)} title={t('changeLook')}>
            <Mascot color={agent.color} animal={animalFor(agent)} status={agent.status} size={96} />
          </button>
          <div className="idc-name">
            <h1>{agent.name}</h1>
            <p>{agent.role}</p>
          </div>
          <button className="btn" onClick={() => dm && go('chats', dm.id)}>{t('chat')}</button>
        </div>
        {faces && (
          <div className="idc-sec">
            <h3>{t('look')}</h3>
            <div className="idc-faces">
              {ANIMALS.map((x) => (
                <button key={x.key} className={animalFor(agent) === x.key ? 'on' : ''} onClick={() => patch({ avatar: x.key })} title={t(`animals.${x.key}`, { defaultValue: x.label })}>
                  <Mascot color={agent.color} animal={x.key} size={44} bubble={false} />
                </button>
              ))}
            </div>
            <div className="swatches mt8">
              {COLORS.map((c) => (
                <button key={c} className={`sw ${agent.color === c ? 'on' : ''}`} style={{ background: c }} onClick={() => patch({ color: c })} />
              ))}
            </div>
          </div>
        )}

        <Editable label={t('job.label')} value={agent.responsibility} placeholder={t('job.placeholder')} onSave={(v) => patch({ responsibility: v })} />

        <div className="idc-grid">
          <Cell ico={<Mail size={17} />} label={t('cells.mail')} value={addr} empty={t('cells.noAddress')} onClick={() => go('settings', 'identity')} />
          <Cell ico={<Phone size={17} />} label={t('cells.phone')} value={phone} empty={t('cells.noNumber')} onClick={() => { location.hash = `#/team/${agent.id}/identity` }} />
          <Cell ico={<Monitor size={17} />} label={t('cells.computer')} value={busy ? agent.status_text || t('cells.working') : t('cells.ready')} onClick={() => dm && go('chats', dm.id)} />
          <Cell ico={<Plug size={17} />} label={t('cells.apps')} value={apps ? t('cells.connected', { n: apps }) : ''} empty={t('cells.noneYet')} onClick={() => go('settings', 'apps')} />
        </div>

        <Editable label={t('knows.label')} value={agent.context} placeholder={t('knows.placeholder')} onSave={(v) => patch({ context: v })} />
        <Editable label={t('boundary.label')} value={agent.boundary} placeholder={t('boundary.placeholder')} onSave={(v) => patch({ boundary: v })} />

        <div className="idc-sec">
          <h3>{t('watching.title')}</h3>
          {watches.length === 0 ? (
            <p className="muted small">{t('watching.empty')}</p>
          ) : (
            <div className="idc-list">
              {watches.map((w) => (
                <div key={w.id} className={`idc-item ${w.enabled ? '' : 'done'}`}>
                  <Eye size={18} />
                  <div className="grow">
                    <b>{w.name}</b>
                    <small>{w.enabled ? t('watching.detail', { filter: w.event_filter, checks: w.checks || 0, every: every(w.every_min) }) : w.outcome || t('watching.done')}</small>
                  </div>
                  {w.enabled ? <button className="icon-btn" title={t('watching.stop')} onClick={() => toggle(w)}><Pause size={15} /></button> : null}
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="idc-sec">
          <h3>{t('routines.title')}</h3>
          {regular.length === 0 ? (
            <p className="muted small">{t('routines.empty')}</p>
          ) : (
            <div className="idc-list">
              {regular.map((r) => (
                <div key={r.id} className={`idc-item ${r.enabled ? '' : 'done'}`}>
                  <Timer size={18} />
                  <div className="grow">
                    <b>{r.name}</b>
                    <small>{r.kind === 'cron' ? `${r.schedule}${r.next_run ? t('routines.next', { when: ago(r.next_run) }) : ''}` : t('routines.when', { filter: r.event_filter })}</small>
                  </div>
                  <button className="icon-btn" title={r.enabled ? t('routines.pause') : t('routines.resume')} onClick={() => toggle(r)}>{r.enabled ? <Pause size={15} /> : <Play size={15} />}</button>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="idc-foot">
          {MORE.map((k) => (
            <button key={k} className={`chip ${tab === k ? 'on' : ''}`} onClick={() => { location.hash = tab === k ? `#/team/${agent.id}` : `#/team/${agent.id}/${k}` }}>{t(`more.${k}`)}</button>
          ))}
          <span className="grow" />
          <button className="btn ghost sm" onClick={letGo}><Trash2 size={14} /> {t('letGo.button')}</button>
        </div>
      </section>

      {tab && (
        <Boundary key={tab + agent.id}>
          {tab === 'memory' && <Memory agentId={agent.id} embedded />}
          {tab === 'skills' && <Skills agentId={agent.id} />}
          {tab === 'identity' && <AgentIdentity agent={agent} hookToken={hookToken} />}
          {tab === 'checkins' && <CheckIns agent={agent} />}
        </Boundary>
      )}
    </div>
  )
}

function Cell({ ico, label, value, empty, onClick }) {
  return (
    <button className="idc-cell" onClick={onClick}>
      <span className="ico">{ico}</span>
      <span className="grow" style={{ minWidth: 0 }}>
        <b>{label}</b>
        {value === null ? <span className="off">…</span> : value ? <span title={value}>{value}</span> : <span className="off">{empty}</span>}
      </span>
    </button>
  )
}

function Editable({ label, value, placeholder, onSave }) {
  const { t } = useTranslation()
  const [edit, setEdit] = useState(false)
  const [v, setV] = useState(value || '')
  useEffect(() => setV(value || ''), [value])
  const save = async () => {
    await onSave(v.trim())
    setEdit(false)
  }
  return (
    <div className="idc-job">
      <label>{label}</label>
      {edit ? (
        <>
          <textarea className="input idc-edit" autoFocus value={v} placeholder={placeholder} onChange={(e) => setV(e.target.value)} />
          <div className="row gap6">
            <button className="btn sm" onClick={save}>{t('common:save')}</button>
            <button className="btn ghost sm" onClick={() => { setV(value || ''); setEdit(false) }}>{t('common:cancel')}</button>
          </div>
        </>
      ) : (
        <div role="button" tabIndex={0} onClick={() => setEdit(true)} style={{ cursor: 'text', whiteSpace: 'pre-wrap' }}>
          {value || <span className="muted">{placeholder}</span>}
        </div>
      )}
    </div>
  )
}

// which connected apps this agent may use (Settings → Apps has the rest)
function AgentApps({ agent }) {
  const { t, i18n } = useTranslation('agent')
  const [d, setD] = useState(null)
  const load = () => api('/api/apps').then(setD).catch(() => {})
  useEffect(() => { load() }, [agent.id])
  if (!d) return null
  const byApp = Object.fromEntries(d.apps.map((a) => [a.id, a]))
  const allowed = (a) => a.agents === 'all' || (a.agents || []).includes(agent.id)
  const toggle = async (a) => {
    const ids = a.agents === 'all' ? null : a.agents || []
    let next
    if (allowed(a)) next = ids === null ? [] : ids.filter((x) => x !== agent.id)
    else next = [...(ids || []), agent.id]
    if (a.agents === 'all') {  // turning one agent off an "everyone" app: keep the others
      const everyone = (await api('/api/agents')).map((x) => x.id)
      next = everyone.filter((x) => x !== agent.id)
    }
    await api(`/api/apps/installed/${a.name}`, { method: 'PATCH', body: { agents: next } })
    load()
  }
  return (
    <div className="card">
      <h3>{t('apps.title')}</h3>
      <p className="muted small">{t('apps.body', { name: agent.name })}</p>
      {d.installed.length === 0 && <p className="small muted">{t('apps.none')}</p>}
      <div className="col gap6 mt8">
        {d.installed.map((a) => {
          const app = byApp[a.app]
          return (
            <label key={a.name} className="row gap8 between">
              <span className="row gap8 min0">
                <AppIcon app={app || { label: a.label }} size={26} />
                <span className="ellipsis">{app ? (app.name[i18n.language] || app.name.en) : a.label}</span>
              </span>
              <span className="switch">
                <input type="checkbox" checked={allowed(a)} onChange={() => toggle(a)} />
                <span />
              </span>
            </label>
          )
        })}
      </div>
      <a className="btn ghost sm mt8" href="#/settings/apps">{t('apps.more')}</a>
    </div>
  )
}

function CheckIns({ agent }) {
  const { t } = useTranslation('agent')
  const [profiles, setProfiles] = useState([])
  const [brain, setBrainState] = useState('default')
  useEffect(() => {
    api('/api/profiles').then((r) => {
      setProfiles(r.profiles || [])
      setBrainState((r.agents || {})[agent.id] || 'default')
    }).catch(() => {})
  }, [agent.id])
  const toggleHB = async () => {
    await api(`/api/agents/${agent.id}`, { method: 'PATCH', body: { heartbeat: !agent.heartbeat } })
    store.set((s) => ({ agents: s.agents.map((x) => (x.id === agent.id ? { ...x, heartbeat: !agent.heartbeat } : x)) }))
  }
  const setBrain = async (profile) => {
    await api(`/api/profiles/agent/${agent.id}`, { method: 'PUT', body: { profile } })
    setBrainState(profile)
    toast(t('checkins.brainSet', { name: agent.name, profile }))
  }
  return (
    <div className="cards">
      <div className="card">
        <h3>{t('checkins.title')}</h3>
        <p className="muted small">{t('checkins.body', { name: agent.name })}</p>
        <label className="row gap8 mt8">
          <span className="switch">
            <input type="checkbox" checked={!!agent.heartbeat} onChange={toggleHB} />
            <span />
          </span>
          <b>{agent.heartbeat ? t('common:on') : t('common:off')}</b>
        </label>
      </div>
      <AgentApps agent={agent} />
      {profiles.length > 1 && (
        <div className="card">
          <h3>{t('checkins.brain')}</h3>
          <p className="muted small">{t('checkins.brainBody', { name: agent.name })}</p>
          <select className="input" value={brain} onChange={(e) => setBrain(e.target.value)}>
            {profiles.map((p) => <option key={p.name} value={p.name}>{p.name} · {p.model}</option>)}
          </select>
        </div>
      )}
    </div>
  )
}
