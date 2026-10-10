import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Eye, Loader2, Pause, Play, Target, Timer, Trash2, X, Zap } from 'lucide-react'
import { api, toast, useStore } from '../store'
import Mascot, { animalFor } from '../components/Mascot'
import { go } from '../App'
import { ago, cronText, routineWhen } from '../util'

// Beside the calendar: what your agents are doing right now, what they keep an eye on,
// and every reminder and routine (one line each, pause or delete right there).

const TIMED = ['once', 'cron', 'event']

export default function PlanSide({ focus }) {
  const { t } = useTranslation('calendar')
  const { agents, running } = useStore()
  const [tasks, setTasks] = useState(null)
  const [autos, setAutos] = useState(null)
  const [showPaused, setShowPaused] = useState(false)

  useEffect(() => {
    const load = () => {
      api('/api/todo').then((r) => setTasks(r.tasks)).catch(() => setTasks([]))
      api('/api/automations').then(setAutos).catch(() => setAutos([]))
    }
    load()
    window.addEventListener('dot:task', load)
    window.addEventListener('dot:automation', load)
    const timer = setInterval(load, 30000)
    return () => {
      window.removeEventListener('dot:task', load)
      window.removeEventListener('dot:automation', load)
      clearInterval(timer)
    }
  }, [])

  const agentById = (id) => agents.find((a) => a.id === id)
  const mine = (x) => agentById(x.agent_id) && (!focus || x.agent_id === focus)
  const live = new Set(Object.values(running).map((r) => r.run_id))
  const doing = (tasks || []).filter((x) => mine(x) && x.kind === 'task' && x.status === 'doing')
    .sort((a, b) => (live.has(b.run_id) - live.has(a.run_id)) || b.updated - a.updated)
  const watching = (tasks || []).filter((x) => mine(x) && (x.kind === 'watch' || x.kind === 'goal') && x.status === 'doing')
  const routines = (autos || []).filter((a) => mine(a) && TIMED.includes(a.kind))
  const on = routines.filter((a) => a.enabled).sort((a, b) => (a.next_run || Infinity) - (b.next_run || Infinity))
  // a one-off that already rang is history (it's in the chat), not something paused
  const paused = routines.filter((a) => !a.enabled && !(a.kind === 'once' && a.last_run))

  const patch = async (a, body) => {
    try {
      await api(`/api/automations/${a.id}`, { method: 'PATCH', body })
      setAutos((xs) => xs.map((x) => (x.id === a.id ? { ...x, ...body } : x)))
    } catch (e) { toast('😵 ' + e.message) }
  }
  const remove = async (a) => {
    if (!window.confirm(t('side.deleteAsk', { name: a.name }))) return
    try {
      await api(`/api/automations/${a.id}`, { method: 'DELETE' })
      setAutos((xs) => xs.filter((x) => x.id !== a.id))
    } catch (e) { toast('😵 ' + e.message) }
  }
  const stopWatch = async (w) => {
    await patch(w, { enabled: 0 })
    setTasks((xs) => xs.filter((x) => x.id !== w.id))
  }
  const fa = focus && agentById(focus)

  return (
    <aside className="plan-side">
      {fa && (
        <button className="cal-chip on plan-focus" onClick={() => go('calendar')}>
          <Mascot color={fa.color} animal={animalFor(fa)} size={18} bubble={false} /> {t('side.onlyAgent', { name: fa.name })} <X size={13} />
        </button>
      )}

      <section className="plan-sec">
        <h3>{t('side.now')} {doing.length > 0 && <small>{doing.length}</small>}</h3>
        {tasks && doing.length === 0 && <p className="plan-empty">{t('side.nowEmpty')}</p>}
        {doing.map((x) => {
          const a = agentById(x.agent_id)
          const p = x.progress || { done: 0, total: 0 }
          const isLive = live.has(x.run_id)
          const step = (x.items || []).find((i) => i.status === 'doing')?.text // the checklist item it's on
          return (
            <button key={x.id} className={`plan-row ${isLive ? 'live' : ''}`} style={{ '--c': a.color }} onClick={() => x.thread_id && go('chats', x.thread_id)}>
              <Mascot color={a.color} animal={animalFor(a)} size={28} bubble={false} status={isLive ? 'working' : 'idle'} />
              <span className="grow plan-txt">
                <b className="ellipsis">{x.title}</b>
                <small className="ellipsis">
                  {isLive && <Loader2 size={11} className="spin" />} {step || (isLive && a.status_text) || a.name}
                  {p.total > 0 && ` · ${p.done}/${p.total}`}
                </small>
              </span>
            </button>
          )
        })}
      </section>

      <section className="plan-sec">
        <h3>{t('side.watching')} {watching.length > 0 && <small>{watching.length}</small>}</h3>
        {tasks && watching.length === 0 && <p className="plan-empty">{t('side.watchingEmpty')}</p>}
        {watching.map((x) => {
          const a = agentById(x.agent_id)
          return (
            <div key={x.id} className="plan-row" style={{ '--c': a.color }}>
              <Mascot color={a.color} animal={animalFor(a)} size={28} bubble={false} />
              <button className="grow plan-txt" onClick={() => x.thread_id && go('chats', x.thread_id)}>
                <b className="ellipsis">{x.kind === 'goal' ? <Target size={12} /> : <Eye size={12} />} {x.title}</b>
                <small className="ellipsis">
                  {x.kind === 'goal' ? `${a.name} · ${x.percent || 0}%` : t('side.watchMeta', { name: a.name, n: x.checks || 0, when: ago(x.updated) })}
                </small>
              </button>
              {x.kind === 'watch' && <button className="icon-btn" title={t('side.stop')} onClick={() => stopWatch(x)}><Pause size={15} /></button>}
            </div>
          )
        })}
      </section>

      <section className="plan-sec">
        <h3>{t('side.reminders')} {on.length > 0 && <small>{on.length}</small>}</h3>
        {autos && on.length === 0 && <p className="plan-empty">{t('side.remindersEmpty')}</p>}
        {on.map((r) => <Routine key={r.id} r={r} agent={agentById(r.agent_id)} onPause={() => patch(r, { enabled: 0 })} onDelete={() => remove(r)} />)}
        {paused.length > 0 && (
          <button className="plan-more" onClick={() => setShowPaused(!showPaused)}>
            {showPaused ? '▾' : '▸'} {t('side.paused', { count: paused.length })}
          </button>
        )}
        {showPaused && paused.map((r) => <Routine key={r.id} r={r} agent={agentById(r.agent_id)} off onResume={() => patch(r, { enabled: 1 })} onDelete={() => remove(r)} />)}
      </section>
    </aside>
  )
}

function Routine({ r, agent, off, onPause, onResume, onDelete }) {
  const { t } = useTranslation('calendar')
  const icon = r.kind === 'event' ? <Zap size={12} /> : <Timer size={12} />
  const when = r.kind === 'once'
    ? t('side.once', { when: routineWhen(r) })
    : r.kind === 'cron'
      ? `${cronText(r.schedule)}${!off && r.next_run ? ` · ${t('side.next', { when: routineWhen(r) })}` : ''}`
      : t('side.onEvent', { filter: r.event_filter })
  return (
    <div className={`plan-row ${off ? 'off' : ''}`} style={{ '--c': agent.color }}>
      <Mascot color={agent.color} animal={animalFor(agent)} size={28} bubble={false} />
      <button className="grow plan-txt" onClick={() => r.thread_id && go('chats', r.thread_id)} title={r.prompt}>
        <b className="ellipsis">{icon} {r.name}</b>
        <small className="ellipsis">{when}</small>
      </button>
      {off
        ? <button className="icon-btn" title={t('side.resume')} onClick={onResume}><Play size={15} /></button>
        : <button className="icon-btn" title={t('side.pause')} onClick={onPause}><Pause size={15} /></button>}
      <button className="icon-btn" title={t('common:delete')} onClick={onDelete}><Trash2 size={15} /></button>
    </div>
  )
}
