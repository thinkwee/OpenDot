import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, toast, useStore } from '../store'
import Mascot, { animalFor } from '../components/Mascot'
import { when, ago } from '../util'
import './core.css'

// [label key under routines:presets, cron]
const PRESETS = [
  ['morning', '0 8 * * *'],
  ['weekdays', '0 9 * * 1-5'],
  ['evening', '0 21 * * *'],
  ['hourly', '0 * * * *'],
  ['mondays', '0 10 * * 1'],
]

// agentId set → embedded in that agent's profile: only its routines/goals, no agent pickers
export default function Automations({ agentId }) {
  const { t } = useTranslation('routines')
  const { agents: allAgents, hookToken } = useStore()
  const agents = agentId ? allAgents.filter((a) => a.id === agentId) : allAgents
  const mine = (x) => !agentId || x.agent_id === agentId
  const [all, setList] = useState([])
  const list = all.filter(mine)
  const [form, setForm] = useState(null)
  const load = () => api('/api/automations').then(setList)
  useEffect(() => {
    load()
  }, [])

  const toggle = async (a) => {
    await api(`/api/automations/${a.id}`, { method: 'PATCH', body: { enabled: a.enabled ? 0 : 1 } })
    load()
  }
  const del = async (a) => {
    await api(`/api/automations/${a.id}`, { method: 'DELETE' })
    load()
  }
  const run = async (a) => {
    await api(`/api/automations/${a.id}/run`, { method: 'POST' })
    toast(t('running', { name: a.name }))
  }
  const hookBase = `${location.origin}/hook/${hookToken}`

  return (
    <div>
      <header className={agentId ? 'row between mb8' : 'page-head'}>
        <div>
          {agentId ? <h3>{t('embeddedTitle')}</h3> : <h1>{t('title')}</h1>}
          {!agentId && <p className="muted">{t('intro')}</p>}
        </div>
        <button className={agentId ? 'btn sm' : 'btn'} onClick={() => setForm({ agent_id: agents[0]?.id, kind: 'cron', name: '', schedule: PRESETS[0][1], prompt: '', event_filter: 'webhook:*' })}>{t('newRoutine')}</button>
      </header>
      <p className="muted small">{t('easiest')}</p>

      <Presets agents={agents} onAdded={load} />

      {list.length === 0 && (
        <div className="empty-card">
          <Mascot animal="fox" color="#FFB38A" status="idle" size={64} />
          <p className="muted">{t('empty')}</p>
        </div>
      )}
      <div className="cards">
        {list.map((a) => {
          const ag = agents.find((x) => x.id === a.agent_id)
          return (
            <div key={a.id} className={`card auto ${a.enabled ? '' : 'off'}`}>
              <div className="row gap8">
                <Mascot color={ag?.color} animal={ag && animalFor(ag)} emoji={ag?.emoji} size={36} bubble={false} />
                <div className="grow">
                  <b>{a.name}</b>
                  <div className="muted small">
                    {a.kind === 'cron' ? t('cronLine', { schedule: a.schedule, when: when(a.next_run) }) : t('eventLine', { filter: a.event_filter })} · {ag?.name}
                  </div>
                </div>
                <label className="switch">
                  <input type="checkbox" checked={!!a.enabled} onChange={() => toggle(a)} />
                  <span />
                </label>
              </div>
              <p className="auto-prompt">{a.prompt}</p>
              <div className="row gap6">
                <button className="btn ghost sm" onClick={() => run(a)}>{t('runNow')}</button>
                <button className="btn ghost sm" onClick={() => del(a)}>{t('common:delete')}</button>
                {a.last_run && <small className="muted">{t('lastRun', { when: when(a.last_run) })}</small>}
              </div>
            </div>
          )
        })}
      </div>

      {!agentId && <WeekCalendar agents={agents} />}
      <Goals agents={agents} mine={mine} />
      <History agents={agents} mine={mine} />

      {!agentId && <section className="section">
        <h3>{t('trigger.title')}</h3>
        <p className="muted small">{t('trigger.body')}</p>
        <pre className="code">{`curl -X POST ${hookBase}/github -H 'Content-Type: application/json' -d '{"text":"New issue #42"}'`}</pre>
      </section>}

      {form && (
        <div className="modal-bg" onClick={() => setForm(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>{t('form.title')}</h2>
            {agents.length > 1 && <label className="lbl">{t('form.who')}</label>}
            <div className="row gap6 wrap" style={agents.length > 1 ? null : { display: 'none' }}>
              {agents.map((a) => (
                <button key={a.id} className={`mini-agent ${form.agent_id === a.id ? 'on' : ''}`} onClick={() => setForm({ ...form, agent_id: a.id })}>
                  <Mascot color={a.color} animal={animalFor(a)} size={22} bubble={false} /> {a.name}
                </button>
              ))}
            </div>
            <input className="input" placeholder={t('form.name')} value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
            <div className="seg">
              <button className={form.kind === 'cron' ? 'on' : ''} onClick={() => setForm({ ...form, kind: 'cron' })}>{t('form.onSchedule')}</button>
              <button className={form.kind === 'event' ? 'on' : ''} onClick={() => setForm({ ...form, kind: 'event' })}>{t('form.onEvent')}</button>
            </div>
            {form.kind === 'cron' ? (
              <>
                <div className="row gap6 wrap">
                  {PRESETS.map(([k, c]) => (
                    <button key={c} className={`chip ${form.schedule === c ? 'on' : ''}`} onClick={() => setForm({ ...form, schedule: c })}>{t(`presets.${k}`)}</button>
                  ))}
                </div>
                <input className="input mono" value={form.schedule} onChange={(e) => setForm({ ...form, schedule: e.target.value })} />
              </>
            ) : (
              <input className="input mono" placeholder="webhook:github · rss:* · share" value={form.event_filter} onChange={(e) => setForm({ ...form, event_filter: e.target.value })} />
            )}
            <textarea className="input" rows={4} placeholder={t('form.prompt')} value={form.prompt} onChange={(e) => setForm({ ...form, prompt: e.target.value })} />
            <div className="row end gap8">
              <button className="btn ghost" onClick={() => setForm(null)}>{t('common:cancel')}</button>
              <button
                className="btn"
                disabled={!form.prompt}
                onClick={async () => {
                  try {
                    await api('/api/automations', { method: 'POST', body: { ...form, name: form.name || form.prompt.slice(0, 30) } })
                    setForm(null)
                    load()
                  } catch (e) {
                    toast(t('form.saveFailed', { msg: e.message }))
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

function Presets({ agents, onAdded }) {
  const { t } = useTranslation('routines')
  const [presets, setPresets] = useState([])
  useEffect(() => {
    api('/api/routines/presets').then(setPresets).catch(() => setPresets([]))
  }, [])
  const add = async (key) => {
    const agent_id = agents[0]?.id
    if (!agent_id) return
    try {
      await api(`/api/routines/presets/${key}`, { method: 'POST', body: { agent_id } })
      toast(t('presetAdded'))
      onAdded()
    } catch (e) {
      toast(t('presetFailed', { msg: e.message }))
    }
  }
  if (presets.length === 0) return null
  return (
    <div className="row gap6 wrap mt8">
      {presets.map((p) => (
        <button key={p.key} className="chip" onClick={() => add(p.key)}>
          {p.key === 'morning_brief' ? '🌅' : '🌙'} {p.name}
        </button>
      ))}
    </div>
  )
}

function WeekCalendar({ agents }) {
  const { t, i18n } = useTranslation('routines')
  const loc = i18n.language === 'zh' ? 'zh-CN' : []
  const [rows, setRows] = useState([])
  useEffect(() => {
    api('/api/routines/upcoming?days=7').then(setRows).catch(() => setRows([]))
  }, [])
  const today = new Date()
  const days = Array.from({ length: 7 }, (_, i) => {
    const d = new Date(today)
    d.setDate(d.getDate() + i)
    return d
  })
  return (
    <section className="section">
      <h3>{t('week.title')}</h3>
      {rows.length === 0 ? (
        <p className="muted small">{t('week.empty')}</p>
      ) : (
        <div className="week-grid">
          {days.map((d, i) => {
            const dayRows = rows.filter((r) => new Date(r.at * 1000).toDateString() === d.toDateString())
            return (
              <div key={i} className={`week-day ${i === 0 ? 'today' : ''}`}>
                <b>{d.toLocaleDateString(loc, { weekday: 'short', day: 'numeric' })}</b>
                {dayRows.map((r, j) => {
                  const ag = agents.find((a) => a.id === r.agent_id)
                  return (
                    <div key={j} className="week-run" title={r.name}>
                      {ag?.emoji} {new Date(r.at * 1000).toLocaleTimeString(loc, { hour: '2-digit', minute: '2-digit' })} {r.name}
                    </div>
                  )
                })}
              </div>
            )
          })}
        </div>
      )}
    </section>
  )
}

function History({ agents, mine }) {
  const { t } = useTranslation('routines')
  const [data, setData] = useState({ messages: [], reports: [] })
  useEffect(() => {
    api('/api/routines/history?limit=40').then(setData).catch(() => {})
  }, [])
  const items = [
    ...data.messages.map((m) => ({ id: m.id, at: m.created, agent_id: m.agent_id, text: m.content, tag: m.meta?.source })),
    ...data.reports.map((r) => ({ id: r.id, at: r.created, agent_id: r.agent_id, text: r.body || r.title, tag: r.title })),
  ].filter(mine).sort((a, b) => b.at - a.at).slice(0, 30)
  return (
    <section className="section">
      <h3>{t('history.title')}</h3>
      {items.length === 0 && <p className="muted small">{t('history.empty')}</p>}
      {items.map((it) => {
        const a = agents.find((x) => x.id === it.agent_id)
        return (
          <div key={it.id} className="history-item">
            <Mascot color={a?.color} animal={a && animalFor(a)} emoji={a?.emoji} size={28} bubble={false} />
            <div className="grow">
              <div className="row between">
                <small className="muted">{it.tag}</small>
                <small className="muted">{ago(it.at)}</small>
              </div>
              <div className="small">{(it.text || '').slice(0, 200)}</div>
            </div>
          </div>
        )
      })}
    </section>
  )
}

function Goals({ agents, mine }) {
  const { t } = useTranslation('routines')
  const [allGoals, setGoals] = useState([])
  const goals = allGoals.filter(mine)
  const [form, setForm] = useState(null)
  const load = () => api('/api/goals').then(setGoals).catch(() => {})
  useEffect(() => {
    load()
  }, [])
  const patch = async (g, fields) => {
    await api(`/api/goals/${g.id}`, { method: 'PATCH', body: fields })
    load()
  }
  const del = async (g) => {
    await api(`/api/goals/${g.id}`, { method: 'DELETE' })
    load()
  }
  return (
    <section className="section">
      <div className="row between">
        <h3>{t('goals.title')}</h3>
        <button className="btn ghost sm" onClick={() => setForm({ agent_id: agents[0]?.id, title: '', why: '', cadence: 1440 })}>{t('goals.new')}</button>
      </div>
      {goals.length === 0 && <p className="muted small">{t('goals.empty')}</p>}
      <div className="cards">
        {goals.map((g) => {
          const a = agents.find((x) => x.id === g.agent_id)
          return (
            <div key={g.id} className="card goal-card">
              <div className="row gap8">
                <Mascot color={a?.color} animal={a && animalFor(a)} emoji={a?.emoji} size={30} bubble={false} />
                <div className="grow">
                  <b>{g.title}</b>
                  {g.why && <div className="muted small goal-why">{g.why}</div>}
                </div>
                <span className={`pill ${g.status === 'done' ? 'ok' : g.status === 'paused' ? 'bad' : ''}`}>{t(`goals.status.${g.status}`, { defaultValue: g.status })}</span>
              </div>
              <div className="goal-meta">
                <div className="goal-bar grow"><i style={{ width: `${g.progress}%` }} /></div>
                <span className="goal-pct">{g.progress}%</span>
              </div>
              {g.notes && <div className="muted small">{g.notes.slice(0, 160)}</div>}
              <div className="row gap6">
                {g.status !== 'paused' ? (
                  <button className="btn ghost sm" onClick={() => patch(g, { status: 'paused' })}>{t('goals.pause')}</button>
                ) : (
                  <button className="btn ghost sm" onClick={() => patch(g, { status: 'active' })}>{t('goals.resume')}</button>
                )}
                {g.status !== 'done' && <button className="btn ghost sm" onClick={() => patch(g, { status: 'done', progress: 100 })}>{t('goals.markDone')}</button>}
                <button className="btn ghost sm" onClick={() => del(g)}>{t('common:delete')}</button>
              </div>
            </div>
          )
        })}
      </div>
      {form && (
        <div className="modal-bg" onClick={() => setForm(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <h2>{t('goals.formTitle')}</h2>
            {agents.length > 1 && (
              <div className="row gap6 wrap">
                {agents.map((a) => (
                  <button key={a.id} className={`mini-agent ${form.agent_id === a.id ? 'on' : ''}`} onClick={() => setForm({ ...form, agent_id: a.id })}>
                    <Mascot color={a.color} animal={animalFor(a)} size={22} bubble={false} /> {a.name}
                  </button>
                ))}
              </div>
            )}
            <input className="input" placeholder={t('goals.titlePh')} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />
            <textarea className="input" rows={2} placeholder={t('goals.whyPh')} value={form.why} onChange={(e) => setForm({ ...form, why: e.target.value })} />
            <label className="lbl">{t('goals.cadence')}</label>
            <input className="input" type="number" value={form.cadence} onChange={(e) => setForm({ ...form, cadence: Number(e.target.value) })} />
            <div className="row end gap8">
              <button className="btn ghost" onClick={() => setForm(null)}>{t('common:cancel')}</button>
              <button
                className="btn"
                disabled={!form.title || !form.agent_id}
                onClick={async () => {
                  try {
                    await api('/api/goals', { method: 'POST', body: form })
                    setForm(null)
                    load()
                  } catch (e) {
                    toast(t('form.saveFailed', { msg: e.message }))
                  }
                }}
              >
                {t('common:save')}
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  )
}
