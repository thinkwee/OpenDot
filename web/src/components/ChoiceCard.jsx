// Cards an agent can put under its message (meta.choices = [{card: …}]):
//   question — a few real options as A, B, C…, pick one (or several) and Continue
//   routine  — "When [every day ▾] at [10:00 ▾], <agent> will […]" → Create
// Answering just sends a normal message, so the rest of the app doesn't care.
import { useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Check, Clock } from 'lucide-react'
import { api, toast } from '../store'
import Mascot, { animalFor } from './Mascot'
import './ChoiceCard.css'

const say = (threadId, text) => api(`/api/threads/${threadId}/messages`, { method: 'POST', body: { text } })

export function focusComposer() {
  const el = document.querySelector('.composer textarea')
  if (el) {
    el.focus()
    el.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }
}

// "B — Porto" / "A + C — Porto, Aveiro" → ['B'] / ['A','C']
function pickedFrom(answer, options) {
  const m = /^\s*([A-F](?:\s*\+\s*[A-F])*)\s*[—-]/.exec(answer || '')
  if (!m) return null
  const keys = m[1].split('+').map((x) => x.trim())
  return keys.every((k) => options.some((o) => o.key === k)) ? keys : null
}

export function QuestionCard({ card, threadId, answer }) {
  const { t } = useTranslation('chats')
  const options = card.options || []
  const [sel, setSel] = useState([])
  const [sent, setSent] = useState(null)
  const said = answer?.content ?? sent
  const done = said != null
  const picked = done ? pickedFrom(said, options) : sel
  const toggle = (k) => {
    if (done) return
    if (card.multi) setSel((s) => (s.includes(k) ? s.filter((x) => x !== k) : [...s, k]))
    else setSel((s) => (s[0] === k ? [] : [k]))
  }
  const go = () => {
    if (!sel.length || done) return
    const chosen = options.filter((o) => sel.includes(o.key))
    const text = `${chosen.map((o) => o.key).join(' + ')} — ${chosen.map((o) => o.label).join(', ')}`
    setSent(text)
    say(threadId, text).catch((e) => {
      setSent(null)
      toast(t('common:errors.sendFailed', { msg: e.message }))
    })
  }
  return (
    <div className={`qcard ${done ? 'done' : ''}`}>
      <div className="qcard-q">{card.question}</div>
      {card.multi && !done && <small className="qcard-sub">{t('card.pickAny')}</small>}
      <div className="qcard-opts" role={card.multi ? 'group' : 'radiogroup'}>
        {options.map((o) => {
          const on = picked?.includes(o.key)
          return (
            <button key={o.key} className={`qopt ${on ? 'on' : ''}`} onClick={() => toggle(o.key)}
              role={card.multi ? 'checkbox' : 'radio'} aria-checked={!!on} disabled={done}>
              <span className="qopt-key">{on ? <Check size={15} strokeWidth={3.4} /> : o.key}</span>
              <span className="qopt-txt">
                <b>{o.label}</b>
                {o.detail && <small>{o.detail}</small>}
              </span>
            </button>
          )
        })}
      </div>
      {done ? (
        !picked && <div className="qcard-own">{t('card.youSaid')} “{said.slice(0, 120)}”</div>
      ) : (
        <div className="qcard-foot">
          <button className="qcard-type" onClick={focusComposer}>{t('card.typeOwn')}</button>
          <button className="btn go" disabled={!sel.length} onClick={go}>{t('card.continue')}</button>
        </div>
      )}
    </div>
  )
}

// ---------------- routine card ----------------
const EVERY = ['day', 'weekdays', 'weekly', 'monthly']
const TIMES = Array.from({ length: 36 }, (_, i) => {
  const m = 6 * 60 + i * 30 // 06:00 … 23:30
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
})
const WD = ['sun', 'mon', 'tue', 'wed', 'thu', 'fri', 'sat']

export function toCron({ every, time, weekday, day }) {
  const [h, m] = (time || '09:00').split(':').map(Number)
  if (every === 'weekdays') return `${m} ${h} * * 1-5`
  if (every === 'weekly') return `${m} ${h} * * ${Math.max(0, WD.indexOf(weekday))}`
  if (every === 'monthly') return `${m} ${h} ${day || 1} * *`
  return `${m} ${h} * * *`
}

export function whenText(t, r) {
  const day = t('routine.dayN', { count: Number(r.day) || 1, ordinal: true })
  return t(`routine.when.${r.every}`, { time: r.time, day, weekday: t(`routine.wd.${r.weekday}`) })
}

// proposal = the agent's suggestion (null when opened from the ＋ menu); answer = the reply to it
export function RoutineCard({ proposal, agents, threadId, answer, onClose }) {
  const { t } = useTranslation('chats')
  const init = proposal || {}
  const [r, setR] = useState({
    every: EVERY.includes(init.every) ? init.every : 'day',
    time: /^\d\d:\d\d$/.test(init.time || '') ? init.time : '09:00',
    weekday: WD.includes(init.weekday) ? init.weekday : 'mon',
    day: init.day || 1,
    prompt: init.prompt || '',
    agent_id: init.agent_id || agents[0]?.id,
  })
  const [state, setState] = useState(null) // null | 'saving' | 'made' | 'skipped'
  const times = useMemo(() => (TIMES.includes(r.time) ? TIMES : [...TIMES, r.time].sort()), [r.time])
  const agent = agents.find((a) => a.id === r.agent_id) || agents[0]
  const said = answer?.content
  const finished = state === 'made' || state === 'skipped' || said != null
  const set = (k) => (e) => setR({ ...r, [k]: e.target.value })

  const create = async () => {
    if (!r.prompt.trim() || !agent) return
    setState('saving')
    const when = whenText(t, r)
    try {
      await api('/api/automations', {
        method: 'POST',
        body: { agent_id: agent.id, kind: 'cron', schedule: toCron(r), thread_id: threadId,
          name: (proposal?.name || r.prompt).trim().slice(0, 40), prompt: r.prompt.trim() },
      })
      setState('made')
      toast(t('routine.made', { when }))
      // an agent asked → tell it how it went; from the ＋ menu → just close
      if (proposal) say(threadId, t('routine.madeMsg', { when, what: r.prompt.trim() })).catch(() => {})
      else onClose?.()
    } catch (e) {
      setState(null)
      toast(t('common:errors.generic', { msg: e.message.slice(0, 120) }))
    }
  }
  const cancel = () => {
    if (!proposal) return onClose?.()
    setState('skipped')
    say(threadId, t('routine.notNowMsg')).catch(() => setState(null))
  }

  if (finished && proposal) {
    const made = state === 'made' || (said || '').startsWith('⏰')
    return (
      <div className={`rcard small ${made ? 'made' : 'skipped'}`}>
        <Clock size={16} strokeWidth={2.6} />
        <span>{made ? (said || t('routine.madeMsg', { when: whenText(t, r), what: r.prompt })) : t('routine.skipped')}</span>
      </div>
    )
  }
  return (
    <div className={`rcard ${proposal ? '' : 'draft'}`}>
      <div className="rcard-head">
        <span className="rcard-ico"><Clock size={16} strokeWidth={2.8} /></span>
        <b>{proposal ? t('routine.titleAgent') : t('routine.title')}</b>
      </div>
      <div className="rcard-line">
        <span>{t('routine.whenLbl')}</span>
        <select className="rsel" value={r.every} onChange={set('every')} aria-label={t('routine.everyLbl')}>
          {EVERY.map((k) => <option key={k} value={k}>{t(`routine.every.${k}`)}</option>)}
        </select>
        {r.every === 'weekly' && (
          <select className="rsel" value={r.weekday} onChange={set('weekday')} aria-label={t('routine.wdLbl')}>
            {WD.slice(1).concat('sun').map((k) => <option key={k} value={k}>{t(`routine.wdOpt.${k}`)}</option>)}
          </select>
        )}
        {r.every === 'monthly' && (
          <select className="rsel" value={r.day} onChange={(e) => setR({ ...r, day: Number(e.target.value) })} aria-label={t('routine.dayLbl')}>
            {Array.from({ length: 28 }, (_, i) => i + 1).map((d) => <option key={d} value={d}>{t('routine.dayN', { count: d, ordinal: true })}</option>)}
          </select>
        )}
        {t('routine.atLbl') && <span>{t('routine.atLbl')}</span>}
        <select className="rsel" value={r.time} onChange={set('time')} aria-label={t('routine.timeLbl')}>
          {times.map((x) => <option key={x} value={x}>{x}</option>)}
        </select>
      </div>
      <div className="rcard-line rwho">
        {agent && <Mascot color={agent.color} animal={animalFor(agent)} emoji={agent.emoji} size={26} bubble={false} />}
        {!proposal && agents.length > 1 ? (
          <select className="rsel" value={r.agent_id} onChange={set('agent_id')} aria-label={t('routine.whoLbl')}>
            {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
          </select>
        ) : (
          <b>{agent?.name}</b>
        )}
        <span>{t('routine.will')}</span>
      </div>
      <textarea className="input rcard-what" rows={Math.min(5, Math.max(2, Math.ceil(r.prompt.length / 34)))} value={r.prompt} onChange={set('prompt')}
        placeholder={t('routine.placeholder')} autoFocus={!proposal} />
      <div className="rcard-foot">
        <small className="muted">{whenText(t, r)}</small>
        <span className="grow" />
        <button className="btn ghost sm" onClick={cancel} disabled={state === 'saving'}>{proposal ? t('routine.notNow') : t('common:cancel')}</button>
        <button className="btn go sm" onClick={create} disabled={!r.prompt.trim() || state === 'saving'}>{t('routine.create')}</button>
      </div>
    </div>
  )
}
