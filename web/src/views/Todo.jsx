import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, Eye, Loader2, Target, X } from 'lucide-react'
import { api, useStore } from '../store'
import Mascot, { animalFor } from '../components/Mascot'
import { go } from '../App'
import { ago } from '../util'
import './Todo.css'

// Everything your agents took on: what needs you, what's moving (and how far along),
// and what's done — one board, one look.

const COLS = ['waiting', 'doing', 'done']
const colOf = (t) => (t.status === 'waiting' ? 'waiting' : t.status === 'doing' ? 'doing' : 'done')

function dayKey(ts, t) {
  const d = new Date(ts * 1000)
  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const diff = Math.floor((today - new Date(d).setHours(0, 0, 0, 0)) / 86400000)
  if (diff <= 0) return t('when.today')
  if (diff === 1) return t('when.yesterday')
  if (diff < 7) return t('when.thisWeek')
  return t('when.earlier')
}

export default function Todo() {
  const { t } = useTranslation('todo')
  const { agents, running, approvals } = useStore()
  const [tasks, setTasks] = useState(null)
  const [who, setWho] = useState(null)
  const [tab, setTab] = useState('doing')
  const wide = window.innerWidth >= 900

  useEffect(() => {
    const load = () => api('/api/todo').then((r) => setTasks(r.tasks)).catch(() => setTasks([]))
    load()
    const onTask = (e) => {
      const tk = e.detail.task
      setTasks((list) => {
        if (!list) return list
        const i = list.findIndex((x) => x.id === tk.id)
        const merged = { kind: 'task', steps: 0, helpers: 0, ...(i >= 0 ? list[i] : {}), ...tk }
        const items = merged.items || []
        merged.progress = { done: items.filter((x) => x.status === 'done').length, total: items.length }
        return i >= 0 ? list.map((x, j) => (j === i ? merged : x)) : [merged, ...list]
      })
    }
    const onAuto = () => load()
    window.addEventListener('dot:task', onTask)
    window.addEventListener('dot:automation', onAuto)
    const timer = setInterval(load, 30000)
    return () => {
      window.removeEventListener('dot:task', onTask)
      window.removeEventListener('dot:automation', onAuto)
      clearInterval(timer)
    }
  }, [])

  const agentById = (id) => agents.find((a) => a.id === id)
  const liveRuns = new Set(Object.values(running).map((r) => r.run_id))
  const visible = (tasks || []).filter((x) => agentById(x.agent_id) && (!who || x.agent_id === who))
  const cols = useMemo(() => {
    const c = { waiting: [], doing: [], done: [] }
    for (const x of visible) c[colOf(x)].push(x)
    c.waiting.sort((a, b) => b.updated - a.updated)
    c.doing.sort((a, b) => (liveRuns.has(b.run_id) - liveRuns.has(a.run_id)) || b.updated - a.updated)
    c.done.sort((a, b) => (b.finished || b.updated) - (a.finished || a.updated))
    return c
  }, [tasks, who, running])
  const pending = approvals.length
  const weekAgo = Date.now() / 1000 - 7 * 86400
  const doneWeek = cols.done.filter((x) => x.status === 'done' && (x.finished || x.updated) > weekAgo).length
  const counts = {}
  for (const x of tasks || []) counts[x.agent_id] = (counts[x.agent_id] || 0) + (colOf(x) === 'done' ? 0 : 1)
  const total = cols.waiting.length + cols.doing.length + doneWeek
  const ring = total ? doneWeek / total : 0

  const column = (k) => (
    <section key={k} className={`todo-col c-${k}`}>
      {wide && (
        <h3 className="todo-col-h">
          <span className="todo-pip" /> {t(`col.${k}`)} <small>{cols[k].length + (k === 'waiting' ? pending : 0)}</small>
        </h3>
      )}
      {k === 'waiting' && pending > 0 && (
        <button className="todo-card approvals" onClick={() => go('inbox')}>
          <b>🙋 {t('approvals', { count: pending })}</b>
          <small className="muted">{t('approvalsSub')}</small>
        </button>
      )}
      {cols[k].length === 0 && !(k === 'waiting' && pending) && <p className="todo-empty">{t(`empty.${k}`)}</p>}
      {k === 'done'
        ? <Done list={cols.done} agentById={agentById} />
        : cols[k].map((x) => <Card key={x.id} x={x} agent={agentById(x.agent_id)} live={liveRuns.has(x.run_id)} />)}
    </section>
  )

  return (
    <div className="todo">
      <header className="todo-head">
        <h1>{t('title')}</h1>
        <div className="todo-stats">
          <div className="todo-ring" style={{ '--p': ring }}>
            <svg viewBox="0 0 36 36"><circle cx="18" cy="18" r="15.5" /><circle cx="18" cy="18" r="15.5" className="on" /></svg>
            <b>{Math.round(ring * 100)}%</b>
          </div>
          <div className="todo-stat s-waiting"><b>{cols.waiting.length + pending}</b><small>{t('col.waiting')}</small></div>
          <div className="todo-stat s-doing"><b>{cols.doing.length}</b><small>{t('col.doing')}</small></div>
          <div className="todo-stat s-done"><b>{doneWeek}</b><small>{t('doneWeek')}</small></div>
        </div>
      </header>

      <div className="todo-who">
        <button className={`cal-chip ${!who ? 'on' : ''}`} onClick={() => setWho(null)}>{t('everyone')}</button>
        {agents.filter((a) => (tasks || []).some((x) => x.agent_id === a.id)).map((a) => (
          <button key={a.id} className={`cal-chip ${who === a.id ? 'on' : ''}`} onClick={() => setWho(who === a.id ? null : a.id)}>
            <Mascot color={a.color} animal={animalFor(a)} size={18} bubble={false} status={a.status} /> {a.name}
            {counts[a.id] > 0 && <small>{counts[a.id]}</small>}
          </button>
        ))}
      </div>

      {!wide && (
        <div className="seg todo-tabs">
          {COLS.map((k) => (
            <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>
              {t(`col.${k}`)} <small>{k === 'done' ? cols.done.length : cols[k].length + (k === 'waiting' ? pending : 0)}</small>
            </button>
          ))}
        </div>
      )}

      {tasks === null ? (
        <div className="todo-loading"><Mascot animal="fox" color="#FFB38A" status="thinking" size={56} /></div>
      ) : (
        <div className="todo-board">{wide ? COLS.map(column) : column(tab)}</div>
      )}
    </div>
  )
}

function Card({ x, agent, live }) {
  const { t } = useTranslation('todo')
  const [more, setMore] = useState(false)
  const items = x.items || []
  const p = x.progress || { done: 0, total: 0 }
  const pct = x.kind === 'goal' ? (x.percent || 0) / 100 : p.total ? p.done / p.total : x.status === 'done' ? 1 : null
  const shown = more ? items : items.slice(0, 5)
  const open = () => x.thread_id && go('chats', x.thread_id)
  const badge = { done: <Check size={13} strokeWidth={3} />, failed: <X size={13} strokeWidth={3} />, stopped: '–' }[x.status]
  return (
    <div role="button" tabIndex={0} className={`todo-card ts-${x.status} k-${x.kind} ${live ? 'live' : ''}`} style={{ '--c': agent.color }} onClick={open}>
      <div className="todo-card-top">
        <Mascot color={agent.color} animal={animalFor(agent)} size={26} bubble={false} status={live ? 'working' : x.status === 'waiting' ? 'waiting' : 'idle'} />
        <small className="todo-agent">{agent.name}</small>
        {x.kind === 'watch' && <span className="todo-kind"><Eye size={12} /> {t('kind.watch')}</span>}
        {x.kind === 'goal' && <span className="todo-kind"><Target size={12} /> {t('kind.goal')}</span>}
        <small className="todo-when">{ago(x.status === 'doing' || x.status === 'waiting' ? x.updated : x.finished || x.updated)}</small>
        {badge && <span className={`todo-badge b-${x.status}`}>{badge}</span>}
      </div>
      <b className="todo-title">{x.title}</b>

      {x.kind === 'watch' && (
        <p className="todo-line">👀 {x.looking_for}</p>
      )}

      {(pct !== null || x.status === 'doing') && x.status !== 'done' && (
        <div className="todo-progress">
          <div className={`todo-bar ${pct === null ? 'indet' : ''}`}><i style={{ width: pct === null ? '40%' : `${Math.max(pct * 100, 4)}%` }} /></div>
          <small>
            {x.kind === 'watch'
              ? t('watchMeta', { n: x.checks || 0, every: Math.round((x.every_min || 60) / 60) || 1 })
              : x.kind === 'goal' ? `${x.percent || 0}%`
                : p.total ? t('of', { done: p.done, total: p.total }) : t('steps', { count: x.steps || 0 })}
            {x.helpers > 0 && ` · ${t('helpers', { count: x.helpers })}`}
          </small>
        </div>
      )}
      {live && agent.status_text && <p className="todo-now"><Loader2 size={12} className="spin" /> {agent.status_text}</p>}

      {items.length > 0 && (
        <ul className="todo-items">
          {shown.map((i, k) => (
            <li key={k} className={`it-${i.status}`}>
              <span className="todo-check">{i.status === 'done' ? <Check size={11} strokeWidth={3.5} /> : i.status === 'doing' ? <i /> : null}</span>
              {i.text}
            </li>
          ))}
          {items.length > 5 && (
            <li className="todo-more" onClick={(e) => { e.stopPropagation(); setMore(!more) }}>
              {more ? t('less') : t('moreItems', { count: items.length - 5 })}
            </li>
          )}
        </ul>
      )}

      {x.status === 'waiting' && <span className="todo-cta">{t('yourTurn')} →</span>}
      {x.result && x.status !== 'waiting' && x.status !== 'doing' && <p className="todo-result">{x.result}</p>}
      {x.status === 'done' && x.steps > 0 && <small className="todo-meta">{t('steps', { count: x.steps })}{x.helpers > 0 && ` · ${t('helpers', { count: x.helpers })}`}{p.total > 0 && ` · ${t('of', { done: p.done, total: p.total })}`}</small>}
    </div>
  )
}

// Done piles up fast: a 14-day strip on top, then one quiet line per job, grouped by
// day — today open, older days folded into "Yesterday · 12" with who did them.
function Done({ list, agentById }) {
  const { t, i18n } = useTranslation('todo')
  const [day, setDay] = useState(null) // a picked day on the strip (ms at local midnight)
  const [openG, setOpenG] = useState({})
  const [shown, setShown] = useState({})
  const [openRow, setOpenRow] = useState(null)
  const locale = i18n.language === 'zh' ? 'zh-CN' : undefined
  const midnight = (ts) => new Date(ts * 1000).setHours(0, 0, 0, 0)
  const today = new Date().setHours(0, 0, 0, 0)
  const strip = Array.from({ length: 14 }, (_, i) => today - (13 - i) * 86400000)
  const perDay = {}
  for (const x of list) perDay[midnight(x.finished || x.updated)] = (perDay[midnight(x.finished || x.updated)] || 0) + 1
  const peak = Math.max(1, ...strip.map((d) => perDay[d] || 0))
  const pool = day ? list.filter((x) => midnight(x.finished || x.updated) === day) : list
  const groups = []
  for (const x of pool) {
    const k = day ? new Date(day).toLocaleDateString(locale, { month: 'short', day: 'numeric', weekday: 'short' }) : dayKey(x.finished || x.updated, t)
    const g = groups.find((y) => y.k === k) || (groups.push({ k, items: [] }), groups[groups.length - 1])
    g.items.push(x)
  }
  return (
    <>
      <div className="done-strip" title={t('strip')}>
        {strip.map((d) => (
          <button key={d} className={`${day === d ? 'on' : ''} ${d === today ? 'today' : ''}`} onClick={() => setDay(day === d ? null : d)}
            title={`${new Date(d).toLocaleDateString(locale, { month: 'short', day: 'numeric' })} · ${perDay[d] || 0}`}>
            <i style={{ height: `${perDay[d] ? 18 + (perDay[d] / peak) * 82 : 6}%` }} />
          </button>
        ))}
      </div>
      {day && <button className="done-clear" onClick={() => setDay(null)}>{t('allDays')} ×</button>}
      {groups.map((g, gi) => {
        const open = openG[g.k] ?? (gi === 0 || !!day)
        const faces = [...new Set(g.items.map((x) => x.agent_id))].map(agentById).filter(Boolean)
        const n = shown[g.k] || 8
        return (
          <div key={g.k} className={`done-group ${open ? 'open' : ''}`}>
            <button className="done-head" onClick={() => setOpenG({ ...openG, [g.k]: !open })}>
              <span className="chev">{open ? '▾' : '▸'}</span>
              <b>{g.k}</b>
              <small>{t('count', { count: g.items.length })}</small>
              <span className="done-faces">
                {faces.slice(0, 5).map((a) => <Mascot key={a.id} color={a.color} animal={animalFor(a)} size={20} bubble={false} />)}
              </span>
            </button>
            {open && (
              <ul className="done-rows">
                {g.items.slice(0, n).map((x) => {
                  const a = agentById(x.agent_id)
                  const expanded = openRow === x.id
                  return (
                    <li key={x.id} className={`done-row r-${x.status} ${expanded ? 'open' : ''}`} style={{ '--c': a.color }}>
                      <button className="done-line" onClick={() => setOpenRow(expanded ? null : x.id)}>
                        <Mascot color={a.color} animal={animalFor(a)} size={20} bubble={false} />
                        <span className="done-title">{x.title}</span>
                        <span className={`done-mark m-${x.status}`}>{x.status === 'done' ? <Check size={11} strokeWidth={3.5} /> : x.status === 'failed' ? <X size={11} strokeWidth={3.5} /> : '–'}</span>
                        <small>{ago(x.finished || x.updated)}</small>
                      </button>
                      {expanded && (
                        <div className="done-more">
                          <small className="muted">{a.name}{x.kind === 'watch' ? ` · ${t('kind.watch')}` : ''}{x.steps ? ` · ${t('steps', { count: x.steps })}` : ''}{x.helpers ? ` · ${t('helpers', { count: x.helpers })}` : ''}</small>
                          {x.result && <p>{x.result}</p>}
                          {x.thread_id && <button className="btn ghost sm" onClick={() => go('chats', x.thread_id)}>{t('openChat')}</button>}
                        </div>
                      )}
                    </li>
                  )
                })}
                {g.items.length > n && (
                  <li><button className="done-moreBtn" onClick={() => setShown({ ...shown, [g.k]: n + 20 })}>{t('showMore', { count: g.items.length - n })}</button></li>
                )}
              </ul>
            )}
          </div>
        )
      })}
    </>
  )
}
