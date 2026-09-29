import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { ChevronLeft, ChevronRight, Eye, Link2, Timer } from 'lucide-react'
import { api, useStore } from '../store'
import Mascot, { animalFor } from '../components/Mascot'
import { go } from '../App'
import './Calendar.css'

// One timeline: your calendars (iPhone-synced, Google / iCloud links) and what your
// agents do (runs, routines, watches) — overlapping, so you can see who's on what.

const HOUR = 46 // px per hour
const DAY = 86400000
const ACCOUNT_COLOR = { Google: '#8EC5FF', iCloud: '#FF9EC4', Outlook: '#9EE3E0', Exchange: '#9EE3E0', iPhone: '#FFD37A' }
const HOW_ICON = { created: '📌', watching: '👀', mentioned: '💬' }

const startOfDay = (d) => { const x = new Date(d); x.setHours(0, 0, 0, 0); return x }
const addDays = (d, n) => { const x = new Date(d); x.setDate(x.getDate() + n); return x }
const startOfWeek = (d) => { const x = startOfDay(d); const wd = (x.getDay() + 6) % 7; return addDays(x, -wd) }
const hm = (ms) => new Date(ms).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })

function load(key, dflt) {
  try { return JSON.parse(localStorage.getItem(key)) ?? dflt } catch { return dflt }
}
function save(key, v) {
  try { localStorage.setItem(key, JSON.stringify(v)) } catch { /* private mode */ }
}

// side-by-side columns for things that overlap in time
function lanes(list) {
  const sorted = [...list].sort((a, b) => a.s - b.s || b.e - a.e)
  const out = []
  let group = []
  let groupEnd = -Infinity
  const flush = () => {
    const cols = []
    for (const it of group) {
      let c = cols.findIndex((end) => end <= it.s)
      if (c === -1) { c = cols.length; cols.push(0) }
      cols[c] = it.e
      it.col = c
    }
    for (const it of group) { it.cols = cols.length; out.push(it) }
    group = []
  }
  for (const it of sorted) {
    if (it.s >= groupEnd && group.length) flush()
    group.push(it)
    groupEnd = Math.max(groupEnd, it.e)
  }
  if (group.length) flush()
  return out
}

export default function Calendar() {
  const { t, i18n } = useTranslation('calendar')
  const { agents } = useStore()
  const wide = window.innerWidth >= 900
  const [mode, setMode] = useState(load('dot_cal_mode', wide ? 'week' : 'day'))
  const [anchor, setAnchor] = useState(startOfDay(new Date()))
  const [data, setData] = useState(null)
  const [hidden, setHidden] = useState(() => new Set(load('dot_cal_hidden', [])))
  const [open, setOpen] = useState(null)
  const scroller = useRef(null)

  const days = useMemo(() => {
    const first = mode === 'week' ? startOfWeek(anchor) : anchor
    return Array.from({ length: mode === 'week' ? 7 : 1 }, (_, i) => addDays(first, i))
  }, [mode, anchor])
  const t0 = days[0].getTime()
  const t1 = addDays(days[days.length - 1], 1).getTime()

  useEffect(() => {
    let off = false
    setData(null)
    api(`/api/agenda?start=${t0 / 1000}&end=${t1 / 1000}`)
      .then((d) => !off && setData(d))
      .catch(() => !off && setData({ events: [], calendars: [], items: [] }))
    return () => { off = true }
  }, [t0, t1])
  useEffect(() => { save('dot_cal_mode', mode) }, [mode])
  useEffect(() => {
    // open at the start of the working day, or just before now
    const h = Math.max(0, Math.min(new Date().getHours() - 2, 7))
    if (scroller.current) scroller.current.scrollTop = Math.max(0, h * HOUR - 12)
  }, [mode, data === null])

  const toggle = (k) => {
    const n = new Set(hidden)
    n.has(k) ? n.delete(k) : n.add(k)
    setHidden(n)
    save('dot_cal_hidden', [...n])
  }
  const agentById = (id) => agents.find((a) => a.id === id)
  const cals = Object.fromEntries((data?.calendars || []).map((c) => [c.key, c]))
  const calColor = (k) => cals[k]?.color || ACCOUNT_COLOR[cals[k]?.account] || '#FFD37A'

  const events = (data?.events || [])
    .filter((e) => !hidden.has('cal:' + e.calendar))
    .map((e) => ({ ...e, type: 'event', s: e.start * 1000, e: e.end * 1000, color: calColor(e.calendar) }))
  const items = (data?.items || [])
    .filter((i) => !hidden.has('agent:' + i.agent_id) && agentById(i.agent_id))
    .map((i) => ({ ...i, type: i.kind, s: i.start * 1000, e: i.end * 1000, color: agentById(i.agent_id)?.color }))
  const allDay = [...events.filter((e) => e.all_day), ...items.filter((i) => i.kind === 'watch')]
  const timed = [...events.filter((e) => !e.all_day), ...items.filter((i) => i.kind !== 'watch')]
  const busyAgents = [...new Set((data?.items || []).map((i) => i.agent_id))].map(agentById).filter(Boolean)
  const linkedAgents = new Set((data?.events || []).flatMap((e) => e.agents.map((x) => x.agent_id)))
  const legendAgents = [...new Map([...busyAgents, ...[...linkedAgents].map(agentById).filter(Boolean)].map((a) => [a.id, a])).values()]

  const locale = i18n.language === 'zh' ? 'zh-CN' : undefined
  const label = mode === 'week'
    ? `${days[0].toLocaleDateString(locale, { month: 'short', day: 'numeric' })} – ${days[6].toLocaleDateString(locale, { month: 'short', day: 'numeric' })}`
    : anchor.toLocaleDateString(locale, { weekday: 'long', month: 'long', day: 'numeric' })
  const step = mode === 'week' ? 7 : 1
  const today = startOfDay(new Date()).getTime()
  const now = Date.now()

  return (
    <div className="cal">
      <header className="cal-head">
        <h1>{t('title')}</h1>
        <div className="cal-nav">
          <button className="icon-btn" onClick={() => setAnchor(addDays(anchor, -step))} aria-label={t('prev')}><ChevronLeft size={18} /></button>
          <button className="btn ghost sm" onClick={() => setAnchor(startOfDay(new Date()))}>{t('today')}</button>
          <button className="icon-btn" onClick={() => setAnchor(addDays(anchor, step))} aria-label={t('next')}><ChevronRight size={18} /></button>
          <b className="cal-label">{label}</b>
        </div>
        <div className="seg">
          <button className={mode === 'day' ? 'on' : ''} onClick={() => setMode('day')}>{t('day')}</button>
          <button className={mode === 'week' ? 'on' : ''} onClick={() => setMode('week')}>{t('week')}</button>
        </div>
      </header>

      {mode === 'day' && (
        <div className="cal-strip">
          {Array.from({ length: 7 }, (_, i) => addDays(startOfWeek(anchor), i)).map((d) => (
            <button key={d.getTime()} className={`${d.getTime() === anchor.getTime() ? 'on' : ''} ${d.getTime() === today ? 'today' : ''}`} onClick={() => setAnchor(d)}>
              <small>{d.toLocaleDateString(locale, { weekday: 'short' })}</small>
              <b>{d.getDate()}</b>
            </button>
          ))}
        </div>
      )}

      <div className="cal-legend">
        <span className="cal-legend-h">{t('yours')}</span>
        {(data?.calendars || []).map((c) => (
          <button key={c.key} className={`cal-chip ${hidden.has('cal:' + c.key) ? 'off' : ''}`} onClick={() => toggle('cal:' + c.key)} title={c.error || ''}>
            <i style={{ background: calColor(c.key) }} />
            {c.name}
            {c.account && c.account !== c.name && <small>{c.account}</small>}
            {c.error && <small className="bad">!</small>}
          </button>
        ))}
        <button className="cal-chip add" onClick={() => go('settings', 'identity')}><Link2 size={13} /> {t('connect')}</button>
        {legendAgents.length > 0 && <span className="cal-legend-h">{t('agents')}</span>}
        {legendAgents.map((a) => (
          <button key={a.id} className={`cal-chip ${hidden.has('agent:' + a.id) ? 'off' : ''}`} onClick={() => toggle('agent:' + a.id)}>
            <Mascot color={a.color} animal={animalFor(a)} size={18} bubble={false} /> {a.name}
          </button>
        ))}
      </div>

      {data && !data.calendars.length && (
        <div className="cal-empty">
          <b>{t('empty.title')}</b>
          <span className="muted">{t('empty.body')}</span>
          <button className="btn sm" onClick={() => go('settings', 'identity')}>{t('empty.cta')}</button>
        </div>
      )}

      <div className="cal-grid" style={{ '--n': days.length }}>
        <div className="cal-row cal-days">
          <div className="cal-gutter" />
          {days.map((d) => (
            <div key={d.getTime()} className={`cal-dayname ${d.getTime() === today ? 'today' : ''}`}>
              <small>{d.toLocaleDateString(locale, { weekday: 'short' })}</small> <b>{d.getDate()}</b>
            </div>
          ))}
        </div>
        {allDay.length > 0 && (
          <div className="cal-row cal-allday">
            <div className="cal-gutter"><small>{t('allDay')}</small></div>
            {days.map((d) => {
              const ds = d.getTime()
              const de = ds + DAY
              return (
                <div key={ds} className="cal-allday-col">
                  {allDay.filter((x) => x.s < de && x.e > ds).map((x) => (
                    <Block key={x.id + ds} x={x} agent={agentById(x.agent_id)} agentById={agentById} onOpen={setOpen} flat />
                  ))}
                </div>
              )
            })}
          </div>
        )}
        <div className="cal-scroll" ref={scroller}>
          <div className="cal-row cal-body" style={{ height: 24 * HOUR }}>
            <div className="cal-gutter">
              {Array.from({ length: 24 }, (_, h) => (
                <span key={h} style={{ top: h * HOUR }}>{h ? `${String(h).padStart(2, '0')}:00` : ''}</span>
              ))}
            </div>
            {days.map((d) => {
              const ds = d.getTime()
              const de = ds + DAY
              const inDay = timed.filter((x) => x.s < de && x.e > ds)
              // your events: side by side where they overlap
              const evs = lanes(inDay.filter((x) => x.type === 'event').map((x) => ({ ...x, s: Math.max(x.s, ds), e: Math.min(Math.max(x.e, x.s + 27 * 60000), de) })))
              // agent markers (a routine firing, a watch check): points in time, so they
              // stack downward instead of splitting the column, and move right of any event
              let floor = -Infinity
              const marks = inDay.filter((x) => x.type !== 'event').sort((a, b) => a.s - b.s).map((x) => {
                const top = Math.max(((x.s - ds) / 3600000) * HOUR, floor + 2)
                floor = top + 22
                const t0 = ds + (top / HOUR) * 3600000
                const clash = evs.some((e) => e.s < t0 + 30 * 60000 && e.e > t0)
                return { ...x, top, clash }
              })
              return (
                <div key={ds} className={`cal-col ${ds === today ? 'today' : ''}`}>
                  {Array.from({ length: 24 }, (_, h) => <i key={h} className="cal-hr" style={{ top: h * HOUR }} />)}
                  {evs.map((x) => (
                    <Block key={x.id} x={x} agentById={agentById} onOpen={setOpen}
                      style={{
                        top: ((x.s - ds) / 3600000) * HOUR,
                        height: Math.max(((x.e - x.s) / 3600000) * HOUR - 2, 20),
                        left: `calc(${(x.col / x.cols) * 100}% + 2px)`,
                        width: `calc(${100 / x.cols}% - 4px)`,
                      }} />
                  ))}
                  {marks.map((x) => (
                    <Block key={x.id} x={x} agent={agentById(x.agent_id)} agentById={agentById} onOpen={setOpen}
                      style={x.clash ? { top: x.top, height: 22, right: 2 } : { top: x.top, height: 20, left: 2, right: 2 }} />
                  ))}
                  {now >= ds && now < de && <div className="cal-now" style={{ top: ((now - ds) / 3600000) * HOUR }} />}
                </div>
              )
            })}
          </div>
        </div>
        {!data && <div className="cal-loading"><Mascot animal="fox" color="#FFB38A" status="thinking" size={48} /></div>}
      </div>

      {open && <Detail x={open} cal={cals[open.calendar]} agentById={agentById} onClose={() => setOpen(null)} />}
    </div>
  )
}

function Block({ x, agent, agentById, onOpen, style, flat }) {
  const { t } = useTranslation('calendar')
  if (x.type === 'event') {
    const who = x.agents.map((l) => agentById(l.agent_id)).filter(Boolean)
    return (
      <button className={`cal-ev ${flat ? 'flat' : ''}`} style={{ ...style, '--c': x.color }} onClick={() => onOpen(x)}>
        <b>{x.title}</b>
        {!flat && <small>{hm(x.s)}{x.location ? ` · ${x.location}` : ''}</small>}
        {who.length > 0 && (
          <span className="cal-who">
            {who.slice(0, 3).map((a) => <Mascot key={a.id} color={a.color} animal={animalFor(a)} size={flat ? 16 : 20} bubble={false} />)}
          </span>
        )}
      </button>
    )
  }
  if (!agent) return null
  const icon = x.type === 'routine' ? <Timer size={12} /> : <Eye size={12} />
  return (
    <button className={`cal-ag cal-${x.type} ${flat ? 'flat' : ''} ${x.past ? 'past' : ''} ${x.clash ? 'mini' : ''}`} style={{ ...style, '--c': agent.color }} onClick={() => onOpen(x)}
      title={`${hm(x.s)} · ${agent.name} · ${x.title}`}>
      <Mascot color={agent.color} animal={animalFor(agent)} size={16} bubble={false} />
      {x.clash ? icon : (
        <span className="cal-ag-txt">
          <b>{icon} {!flat && <span className="cal-time">{hm(x.s)}</span>} {x.title}</b>
        </span>
      )}
    </button>
  )
}

function Detail({ x, cal, agentById, onClose }) {
  const { t, i18n } = useTranslation('calendar')
  const locale = i18n.language === 'zh' ? 'zh-CN' : undefined
  const agent = agentById(x.agent_id)
  const range = `${new Date(x.start * 1000).toLocaleString(locale, { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })} – ${hm(x.end * 1000)}`
  return createPortal(
    <div className="modal-bg" onClick={onClose}>
      <div className="modal cal-detail" onClick={(e) => e.stopPropagation()} style={{ '--c': x.color || agent?.color }}>
        <div className="cal-detail-bar" />
        {x.type === 'event' ? (
          <>
            <h2>{x.title}</h2>
            <p className="muted small">{x.all_day ? t('allDay') : range}</p>
            {cal && <p className="small"><i className="cal-dot" style={{ background: x.color }} /> {cal.name}{cal.account && cal.account !== cal.name ? ` · ${cal.account}` : ''}{cal.source === 'iphone' ? ` · ${t('fromPhone')}` : ''}</p>}
            {x.location && <p className="small">📍 {x.location}</p>}
            {x.notes && <p className="small muted cal-notes">{x.notes}</p>}
            <h4>{t('whoOnIt')}</h4>
            {x.agents.length === 0 ? (
              <p className="muted small">{t('nobody')}</p>
            ) : (
              <div className="idc-list">
                {x.agents.map((l) => {
                  const a = agentById(l.agent_id)
                  if (!a) return null
                  return (
                    <button key={l.agent_id + l.how} className="idc-item" onClick={() => l.thread_id && go('chats', l.thread_id)}>
                      <Mascot color={a.color} animal={animalFor(a)} size={30} bubble={false} />
                      <div className="grow"><b>{a.name}</b><small>{HOW_ICON[l.how]} {t(`how.${l.how}`)}</small></div>
                    </button>
                  )
                })}
              </div>
            )}
          </>
        ) : (
          <>
            <div className="row gap8">
              {agent && <Mascot color={agent.color} animal={animalFor(agent)} size={44} bubble={false} />}
              <div>
                <h2>{x.title}</h2>
                <p className="muted small">{agent?.name} · {t(`kind.${x.type}`)}{x.past ? ` · ${t('done')}` : ''}</p>
                <p className="muted small">{x.type === 'watch' ? t('everyMin', { n: x.every_min || 60 }) : range.split(' – ')[0]}</p>
              </div>
            </div>
            {(x.type === 'watch' || x.type === 'check') && (
              <p className="small">👀 {x.looking_for} <span className="muted">· {t('everyMin', { n: x.every_min || 60 })} · {t('checks', { n: x.checks })}</span></p>
            )}
            <div className="row gap8 mt8">
              {x.thread_id && <button className="btn sm" onClick={() => go('chats', x.thread_id)}>{t('openChat')}</button>}
              {agent && <button className="btn ghost sm" onClick={() => go('team', agent.id)}>{t('openCard')}</button>}
            </div>
          </>
        )}
        <div className="row end mt8"><button className="btn ghost sm" onClick={onClose}>{t('common:close')}</button></div>
      </div>
    </div>,
    document.body,
  )
}

// Settings → your calendar links (Google / iCloud "secret address in iCal format")
export function MyCalendars() {
  const { t } = useTranslation('calendar')
  const [list, setList] = useState(null)
  const [url, setUrl] = useState('')
  const [name, setName] = useState('')
  const [msg, setMsg] = useState('')
  useEffect(() => { api('/api/agenda/feeds').then(setList).catch(() => setList([])) }, [])
  if (!list) return <div className="card">…</div>
  const put = async (next) => setList(await api('/api/agenda/feeds', { method: 'PUT', body: next }))
  const add = async () => {
    setMsg(t('feeds.checking'))
    const r = await api('/api/agenda/feeds/test', { method: 'POST', body: { url } })
    if (!r.ok) return setMsg(t('feeds.bad', { err: r.error }))
    await put([...list, { url, name: name || r.account }])
    setUrl('')
    setName('')
    setMsg(t('feeds.added', { n: r.count }))
  }
  return (
    <div className="card" style={{ gridColumn: '1 / -1' }}>
      <h3>{t('feeds.title')}</h3>
      <p className="muted small">{t('feeds.body')}</p>
      {list.map((f, i) => (
        <div key={i} className="idc-item">
          <i className="cal-dot" style={{ background: f.color || ACCOUNT_COLOR[f.account] || '#FFD37A' }} />
          <div className="grow" style={{ minWidth: 0 }}><b>{f.name}</b><small className="ellipsis">{f.account || f.url}</small></div>
          <button className="btn ghost sm" onClick={() => put(list.filter((_, j) => j !== i))}>{t('feeds.remove')}</button>
        </div>
      ))}
      <div className="row gap8 wrap mt8">
        <input className="input" style={{ flex: '1 1 260px' }} placeholder={t('feeds.url')} value={url} onChange={(e) => setUrl(e.target.value)} />
        <input className="input" style={{ flex: '0 1 160px' }} placeholder={t('feeds.name')} value={name} onChange={(e) => setName(e.target.value)} />
        <button className="btn" disabled={!url.trim()} onClick={add}>{t('feeds.add')}</button>
      </div>
      {msg && <p className="small mt8">{msg}</p>}
      <details className="mt8">
        <summary className="muted small">{t('feeds.how')}</summary>
        <p className="muted small">{t('feeds.howGoogle')}</p>
        <p className="muted small">{t('feeds.howIcloud')}</p>
        <p className="muted small">{t('feeds.howPhone')}</p>
      </details>
    </div>
  )
}
