import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { ArrowUp, Loader2, LogOut, Monitor, Paperclip, Plus, Square, Upload, X } from 'lucide-react'
import { useDropzone } from 'react-dropzone'
import { api, getToken, loadThread, store, useStore, toast } from '../store'
import Mascot, { animalFor, helperAnimal } from '../components/Mascot'
import Deco from '../components/Deco'
import DeliverableCard, { fmtSize } from '../components/Deliverable'
import { FileIcon, agentFile, openFile } from '../components/FileViewer'
import { QuestionCard, RoutineCard } from '../components/ChoiceCard'
import { AgentStack } from './ChatList'
import { AppOffer } from './Apps'
import { go } from '../App'
import { md, stepLabel } from '../util'
import './core.css'
import './soul.css'

// starter requests for an empty chat live in chats.json (`suggest.dm`, `suggest.group`)

// paths an agent mentions (`shared/notes.md`, `~/report.xlsx`) become clickable file chips
const PATH_RX = /^(~\/|\.\/|\/)?([\w\-.()一-鿿 ]+\/)*[\w\-.()一-鿿]+\.[A-Za-z0-9]{1,8}$/
function linkFiles(html) {
  return html.replace(/<code>([^<]{3,200})<\/code>/g, (m, inner) => {
    const t = inner.trim()
    if (!PATH_RX.test(t) || /^\d+(\.\d+)+$/.test(t) || /^[\w-]+\.(com|org|net|io|ai|dev|cn|uk)$/i.test(t)) return m
    return `<code class="file-link" data-path="${t.replace(/"/g, '&quot;')}" role="button" tabindex="0">${inner}</code>`
  })
}
function onFileClick(e, agentId) {
  const el = e.target.closest?.('.file-link')
  if (!el || !agentId) return
  e.preventDefault()
  openFile(agentFile(agentId, el.dataset.path))
}

// ---- uploads ----
export const LONG_PASTE = 4000
function uploadFile(file, threadId, onProgress) {
  return new Promise((resolve, reject) => {
    const fd = new FormData()
    fd.append('file', file, file.name)
    fd.append('thread_id', threadId)
    const x = new XMLHttpRequest()
    x.open('POST', '/api/uploads')
    x.setRequestHeader('Authorization', `Bearer ${getToken()}`)
    x.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total)
    x.onload = () => (x.status < 300 ? resolve(JSON.parse(x.responseText)) : reject(new Error(x.responseText || x.statusText)))
    x.onerror = () => reject(new Error('network error'))
    x.send(fd)
  })
}
function useUploads(threadId) {
  const [items, setItems] = useState([])
  const { t } = useTranslation('chats')
  useEffect(() => {
    const on = (e) => {
      const u = e.detail?.upload
      if (u) setItems((xs) => xs.map((x) => (x.att?.id === u.id ? { ...x, att: u } : x)))
    }
    window.addEventListener('dot:upload', on)
    return () => window.removeEventListener('dot:upload', on)
  }, [])
  const add = (files) => {
    for (const file of files) {
      const key = Math.random().toString(36).slice(2)
      setItems((xs) => [...xs, { key, name: file.name, size: file.size, progress: 0 }])
      uploadFile(file, threadId, (p) => setItems((xs) => xs.map((x) => (x.key === key ? { ...x, progress: p } : x))))
        .then((att) => setItems((xs) => xs.map((x) => (x.key === key ? { ...x, att, progress: 1 } : x))))
        .catch((err) => {
          toast(t('chat.uploadFailed', { name: file.name, msg: err.message.slice(0, 120) }))
          setItems((xs) => xs.filter((x) => x.key !== key))
        })
    }
  }
  const remove = (key) => setItems((xs) => xs.filter((x) => x.key !== key))
  return { items, add, remove, clear: () => setItems([]) }
}
function pastedFile(text) {
  const md = /^#{1,6} |\n[-*] |```/m.test(text)
  const d = new Date()
  const stamp = `${d.getHours()}${String(d.getMinutes()).padStart(2, '0')}${String(d.getSeconds()).padStart(2, '0')}`
  return new File([text], `pasted-${stamp}.${md ? 'md' : 'txt'}`, { type: md ? 'text/markdown' : 'text/plain' })
}

export default function ChatView({ threadId, desktop, computerOpen, onToggleComputer }) {
  const { threads, agents, messages, steps, running, approvals, workers, thoughts, streams } = useStore()
  const thread = threads.find((t) => t.id === threadId)
  const msgs = messages[threadId]
  const tsteps = steps[threadId] || []
  const endRef = useRef(null)
  const { t } = useTranslation('chats')
  const uploads = useUploads(threadId)
  const { getRootProps, getInputProps, isDragActive, open: pickFiles } = useDropzone({
    onDrop: (fs) => fs.length && uploads.add(fs), noClick: true, noKeyboard: true, multiple: true,
  })

  useEffect(() => {
    loadThread(threadId)
  }, [threadId])

  const members = useMemo(() => (thread?.members || []).map((id) => agents.find((a) => a.id === id)).filter(Boolean), [thread, agents])
  const live = members.filter((a) => running[a.id]?.thread_id === threadId)
  const myApprovals = approvals.filter((a) => a.thread_id === threadId)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [msgs?.length, tsteps.length, live.length, myApprovals.length, Object.keys(streams).length])

  if (!thread) return <div className="chat"><div className="empty-main">{t('chat.notFound')}</div></div>

  const byRun = {}
  for (const s of tsteps) (byRun[s.run_id] ||= []).push(s)
  // one timeline, in the order things happened: messages (yours included, even mid-run)
  // and the agents' steps between them; only what's happening right now sits at the end
  const timeline = []
  const events = [...(msgs || []).map((m) => ({ at: m.created, m })), ...tsteps.map((st) => ({ at: st.created, st }))]
    .sort((x, y) => x.at - y.at)
  for (const e of events) {
    const last = timeline[timeline.length - 1]
    if (e.m) timeline.push({ m: e.m })
    else if (last?.run === e.st.run_id) last.steps.push(e.st)
    else timeline.push({ run: e.st.run_id, steps: [e.st], key: e.st.id })
  }
  const lastSegment = {}
  timeline.forEach((x, i) => { if (x.run) lastSegment[x.run] = i })
  const lead = members[0]
  const busy = live.length > 0

  return (
    <section className={`chat ${isDragActive ? 'dragging' : ''}`} {...getRootProps()}>
      <input {...getInputProps()} />
      {isDragActive && (
        <div className="drop-hint"><Deco preset="card" seed={2} /><Upload size={36} strokeWidth={2.4} /><b>{t('chat.drop')}</b><small>{t('chat.dropSub')}</small></div>
      )}
      <header className="chat-head">
        {!desktop && <button className="icon-btn" onClick={() => go('chats')}>‹</button>}
        <button className="chat-who" onClick={() => thread.kind === 'dm' && lead && go('team', lead.id)} title={thread.kind === 'dm' ? t('chat.card', { name: lead?.name }) : ''}>
        <AgentStack members={thread.members} agents={agents} size={42} />
        <div className="chat-title">
          <b>{thread.title}</b>
          <small className="muted ellipsis">
            {busy
              ? live.map((a) => `${a.name}: ${a.status_text || t('common:status.thinking')}`).join(' · ')
              : thread.kind === 'group'
                ? members.map((m) => m.name).join(', ')
                : lead?.responsibility || lead?.tagline || lead?.role}
          </small>
        </div>
        </button>
        <FilesButton threadId={threadId} />
        {thread.kind === 'group' && (
          <button className="btn ghost sm" title={t('chat.dissolve')} onClick={async () => {
            if (!window.confirm(t('chat.dissolveAsk', { title: thread.title }))) return
            try {
              await api(`/api/threads/${threadId}`, { method: 'DELETE' })
              store.set((s) => ({ threads: s.threads.filter((x) => x.id !== threadId) }))
              toast(t('chat.dissolved', { title: thread.title }))
              go('chats')
            } catch (e) { toast('😵 ' + e.message) }
          }}>
            <LogOut size={15} /> <span className="hide-sm">{t('chat.dissolve')}</span>
          </button>
        )}
        <button className={`btn ghost sm ${computerOpen ? 'on' : ''}`} onClick={onToggleComputer} title={t('chat.computerTitle')}>
          <Monitor size={15} /> <span className="hide-sm">{t('chat.computer')}</span>
        </button>
      </header>

      <div className="chat-body">
        {msgs && msgs.length === 0 && (
          <div className="hello">
            <Deco seed={3} />
            <Mascot color={lead?.color} animal={lead && animalFor(lead)} emoji={lead?.emoji} size={96} status="idle" />
            <h2>{thread.kind === 'group' ? t('chat.welcomeGroup', { title: thread.title }) : t('chat.hiDm', { name: lead?.name })}</h2>
            <p className="muted">
              {thread.kind === 'group'
                ? t('chat.groupIntro', { names: members.map((m) => m.name).join(', '), lead: lead?.name })
                : t('chat.dmIntro', { tagline: lead?.tagline })}
            </p>
            <div className="suggest">
              {t(thread.kind === 'group' ? 'suggest.group' : 'suggest.dm', { returnObjects: true }).map((s) => (
                <SuggestChip key={s} text={s} threadId={threadId} />
              ))}
            </div>
          </div>
        )}
        {timeline.map((x, i) => {
          if (x.run) {
            const a = agents.find((g) => g.id === (x.steps[0].agent_id || '').replace(HELPER, ''))
            const owner = live.find((g) => running[g.id]?.run_id === x.run)
            const now = owner && lastSegment[x.run] === i // only the latest part of a live run is "working"
            const ws = now ? Object.values(workers).filter((w) => w.parent_id === owner.id && (w.run_id ? w.run_id === x.run : w.thread_id === threadId)) : []
            return <RunCard key={x.key} steps={x.steps} all={byRun[x.run]} agent={a} live={now} workers={ws} />
          }
          const m = x.m
          const a = agents.find((g) => g.id === m.agent_id)
          const answer = m.meta?.choices?.length ? msgs.slice(msgs.indexOf(m) + 1).find((y) => y.role === 'user') : null
          return <Bubble key={m.id} m={m} agent={a} group={thread.kind === 'group'} lead={lead} threadId={threadId} answer={answer} members={members} />
        })}
        {live.map((a) => {
          const r = running[a.id]
          return (
            <div key={a.id} className="live">
              <div className="msg agent" style={{ '--c': a.color }}>
                <Mascot color={a.color} animal={animalFor(a)} emoji={a.emoji} status={a.status} size={34} bubble={false} />
                {streams[r.run_id] ? (
                  <div className="bubble-wrap">
                    <div className="bubble streaming" dangerouslySetInnerHTML={{ __html: md(streams[r.run_id]) }} />
                  </div>
                ) : (
                  <div className="bubble typing">
                    {thoughts[r.run_id] && <div className="thought">{thoughts[r.run_id].slice(0, 200)}</div>}
                    <span className="dots"><i /><i /><i /></span>
                    <small className="muted">{a.status_text}</small>
                    {!computerOpen && <button className="link-btn watch" onClick={onToggleComputer}>🖥️ {t('chat.watch')}</button>}
                  </div>
                )}
              </div>
            </div>
          )
        })}
        {myApprovals.map((ap) => (
          <ApprovalCard key={ap.id} ap={ap} agent={agents.find((a) => a.id === ap.agent_id)} />
        ))}
        <div ref={endRef} />
      </div>

      <Composer threadId={threadId} members={members} group={thread.kind === 'group'} busy={busy} uploads={uploads} pickFiles={pickFiles} />
    </section>
  )
}

function SuggestChip({ text, threadId }) {
  return (
    <button className="chip" onClick={() => api(`/api/threads/${threadId}/messages`, { method: 'POST', body: { text } })}>
      {text}
    </button>
  )
}

function Choices({ options, threadId, answer }) {
  const { t } = useTranslation('chats')
  const [picked, setPicked] = useState(null)
  const chosen = answer?.content ?? picked
  const pick = (o) => {
    if (chosen) return
    setPicked(o)
    api(`/api/threads/${threadId}/messages`, { method: 'POST', body: { text: o } }).catch((e) => {
      setPicked(null)
      toast(t('common:errors.sendFailed', { msg: e.message }))
    })
  }
  return (
    <div className={`choices ${chosen ? 'used' : ''}`}>
      {options.map((o) => (
        <button key={o} className={`choice ${chosen === o ? 'picked' : ''}`} onClick={() => pick(o)}>{o}</button>
      ))}
    </div>
  )
}

// meta.choices: ["Yes", "Not now"] → chips; [{card: 'question' | 'routine', …}] → a card
function Reply({ choices, agent, members, threadId, answer }) {
  const card = typeof choices[0] === 'object' ? choices[0] : null
  if (!card) return <Choices options={choices} threadId={threadId} answer={answer} />
  if (card.card === 'question') return <QuestionCard card={card} threadId={threadId} answer={answer} />
  if (card.card === 'app') return <AppOffer card={card} agent={agent} />
  if (card.card === 'routine') {
    const who = [agent, ...(members || [])].filter(Boolean)
    return <RoutineCard proposal={card} agents={who} threadId={threadId} answer={answer} />
  }
  return null
}

function Bubble({ m, agent, group, lead, threadId, answer, members }) {
  const { t } = useTranslation('chats')
  if (m.role === 'user') {
    const atts = m.meta?.attachments || []
    return (
      <div className="msg user">
        <div className="bubble-wrap user-wrap">
          {atts.length > 0 && <div className="user-files">{atts.map((d) => <DeliverableCard key={d.id} d={d} />)}</div>}
          {m.content && <div className="bubble" onClick={(e) => onFileClick(e, lead?.id)} dangerouslySetInnerHTML={{ __html: linkFiles(md(m.content)) }} />}
        </div>
      </div>
    )
  }
  const auto = m.meta?.source && !['chat', 'hello'].includes(m.meta.source)
  return (
    <div className="msg agent" style={{ '--c': agent?.color }}>
      <Mascot color={agent?.color} animal={agent && animalFor(agent)} emoji={agent?.emoji} size={34} bubble={false} />
      <div className="bubble-wrap">
        {(group || auto) && (
          <small className="who" style={{ color: agent?.color }}>
            {agent?.name}
            {auto && <span className="src-tag">{m.meta.source.replace('automation:', '⏰ ').replace('heartbeat', t('chat.src.heartbeat')).replace('handoff', t('chat.src.handoff')).replace('watch:', '👀 ')}</span>}
          </small>
        )}
        <div className="bubble" onClick={(e) => onFileClick(e, agent?.id)} onKeyDown={(e) => e.key === 'Enter' && onFileClick(e, agent?.id)}
          dangerouslySetInnerHTML={{ __html: linkFiles(md(m.content)) }} />
        {(m.meta?.attachments || []).length > 0 && (
          <div className="agent-files">{m.meta.attachments.map((d) => <DeliverableCard key={d.id} d={d} />)}</div>
        )}
        {m.meta?.choices?.length > 0 && <Reply choices={m.meta.choices} agent={agent} members={members} threadId={threadId} answer={answer} />}
      </div>
    </div>
  )
}

// Helpers share the lead's run; their steps carry agent ids like "<lead>-w2".
const HELPER = /-w(\d+)$/

function helperTitles(steps) {
  // titles come from the lead's latest `delegate` call (tasks[i] ↔ helper wi)
  for (let i = steps.length - 1; i >= 0; i--) {
    const s = steps[i]
    if (s.tool !== 'delegate') continue
    let tasks = s.args?.tasks
    if (typeof tasks === 'string') {
      try { tasks = JSON.parse(tasks) } catch { tasks = null }
    }
    if (Array.isArray(tasks)) return { titles: tasks.map((x) => x?.title || ''), done: s.status !== 'running' }
  }
  return { titles: [], done: true }
}

function RunCard({ steps, all = steps, agent, live, workers = [] }) {
  const [open, setOpen] = useState(!!live)
  const { t } = useTranslation('chats')
  useEffect(() => {
    setOpen(!!live)
  }, [live])
  const own = steps.filter((s) => !HELPER.test(s.agent_id || ''))
  const helpers = {}
  for (const s of steps) {
    const m = (s.agent_id || '').match(HELPER)
    if (m) (helpers[m[1]] ||= []).push(s)
  }
  const idx = Object.keys(helpers).map(Number)
  // helper names come from the delegate step, which may sit in an earlier part of the run
  const { titles, done: allDone } = helperTitles(all.filter((s) => !HELPER.test(s.agent_id || '')))
  for (let i = 0; i < titles.length; i++) if (!(i in helpers) && live && !allDone) idx.push(i)
  const team = [...new Set(idx)].sort((a, b) => a - b).map((i) => {
    const w = workers.find((x) => x.worker_id?.endsWith(`-w${i}`))
    return { i, title: titles[i] || w?.title || t('team.helper', { n: i + 1 }), steps: helpers[i] || [], done: allDone || w?.state === 'done' }
  })
  const errs = steps.filter((s) => s.status === 'error').length
  return (
    <div className={`run ${live ? 'live' : ''}`} style={{ '--c': agent?.color }}>
      <button className="run-head" onClick={() => setOpen(!open)}>
        <span className="run-dot" />
        <span>
          {t(live ? 'run.working' : 'run.used', { name: agent?.name })} · {t('run.steps', { count: steps.length })}
          {errs > 0 && <span className="err-tag"> · {t('run.hiccups', { count: errs })}</span>}
        </span>
        <span className="chev">{open ? '▾' : '▸'}</span>
      </button>
      {open && (
        <ol className="steps">
          {own.map((s) => (
            <Step key={s.id} s={s} />
          ))}
        </ol>
      )}
      {team.length > 0 && <Team team={team} agent={agent} live={live} />}
    </div>
  )
}

// The helpers a lead sent off in parallel: each its own face, job and progress.
function Team({ team, agent, live }) {
  const { t } = useTranslation('chats')
  const [openI, setOpenI] = useState(null)
  const doneN = team.filter((h) => h.done).length
  return (
    <div className="team">
      <div className="team-head">
        <b>{t('team.title', { count: team.length })}</b>
        <small className="muted">{doneN === team.length ? t('team.allDone') : t('team.progress', { done: doneN, count: team.length })}</small>
      </div>
      {team.map((h) => {
        const last = h.steps[h.steps.length - 1]
        const now = last ? stepLabel(last) : null
        const working = live && !h.done
        return (
          <div key={h.i} className={`helper ${h.done ? 'done' : ''}`}>
            <button className="helper-row" onClick={() => setOpenI(openI === h.i ? null : h.i)}>
              <Mascot color={agent?.color} animal={helperAnimal(agent, h.i)} status={working ? 'working' : 'idle'} size={30} bubble={false} />
              <span className="grow helper-txt">
                <b className="ellipsis">{h.title}</b>
                <small className="muted ellipsis">
                  {h.done ? t('team.doneSteps', { count: h.steps.length }) : now ? `${now.icon} ${now.verb} ${now.obj || ''}` : t('team.starting')}
                </small>
              </span>
              <span role="button" tabIndex={0} className="helper-pc" title={t('team.watch')}
                onClick={(e) => { e.stopPropagation(); store.set({ watchOwner: `${agent.id}-w${h.i}` }) }}>
                <Monitor size={14} />
              </span>
              <span className={`helper-st ${h.done ? 'ok' : ''}`}>{h.done ? '✓' : <Loader2 size={14} className="spin" />}</span>
            </button>
            {openI === h.i && h.steps.length > 0 && (
              <ol className="steps">
                {h.steps.map((s) => <Step key={s.id} s={s} />)}
              </ol>
            )}
          </div>
        )
      })}
    </div>
  )
}

function Step({ s }) {
  const [open, setOpen] = useState(false)
  const { icon, verb, obj } = stepLabel(s)
  return (
    <li className={`step ${s.status}`}>
      <button onClick={() => setOpen(!open)}>
        <span className="step-ico">{icon}</span>
        <span className="step-verb">{verb}</span>
        <code className="ellipsis">{obj}</code>
        <span className={`st st-${s.status}`}>{s.status === 'running' ? '…' : s.status === 'waiting' ? '⏸' : s.status === 'error' ? '!' : s.status === 'stopped' ? '■' : '✓'}</span>
      </button>
      {open && s.result && <pre className="step-out">{prettyResult(s.result)}</pre>}
    </li>
  )
}

function prettyResult(r) {
  try {
    const o = JSON.parse(r)
    return o.output ?? o.content ?? o.text ?? JSON.stringify(o, null, 2)
  } catch {
    return r
  }
}

export function ApprovalCard({ ap, agent }) {
  const [more, setMore] = useState(false)
  const { t } = useTranslation('chats')
  const decide = (approve, scope) =>
    api(`/api/gatekeeper/approvals/${ap.id}/decide`, { method: 'POST', body: { approve, scope } }).then(() =>
      toast(approve ? t('approval.toastGo') : t('approval.toastSkip')),
    )
  const args = ap.args || {}
  const domain = args._domain
  const what = args._what || ap.tool.replace(/_/g, ' ')
  const detail = args.body || args.message || args.command || args.code
  return (
    <div className="approval friendly">
      <div className="row gap8">
        <Mascot color={agent?.color} animal={agent && animalFor(agent)} status="waiting" size={44} />
        <div className="grow">
          <div className="ap-say">{t('approval.ask', { what })}</div>
          {ap.reason && <div className="muted small">{ap.reason}</div>}
        </div>
      </div>
      <details>
        <summary>{t('approval.seeWhat')}</summary>
        <pre className="ap-args">{detail || JSON.stringify(Object.fromEntries(Object.entries(args).filter(([k]) => !k.startsWith('_'))), null, 2)}</pre>
      </details>
      <div className="row gap6 wrap">
        <button className="btn go" onClick={() => decide(true, 'once')}>{t('approval.go')}</button>
        <button className="btn ghost" onClick={() => decide(false, 'deny')}>{t('approval.notNow')}</button>
        <button className="btn ghost sm more-btn" onClick={() => setMore(!more)}>{more ? t('approval.less') : '…'}</button>
      </div>
      {more && (
        <div className="row gap6 wrap">
          <button className="chip" onClick={() => decide(true, domain ? 'domain' : 'always')}>{domain ? t('approval.dontAskFor', { domain }) : t('approval.dontAsk')}</button>
          <button className="chip" onClick={() => decide(true, 'session')}>{t('approval.okChat')}</button>
          {domain && <button className="chip" onClick={() => decide(true, 'always')}>{t('approval.neverAsk')}</button>}
        </div>
      )}
    </div>
  )
}

function Composer({ threadId, members, group, busy, uploads, pickFiles }) {
  const [text, setText] = useState('')
  const [pick, setPick] = useState(null) // {query, start, index} while typing "@…"
  const [plus, setPlus] = useState(false) // the ＋ menu
  const [routine, setRoutine] = useState(false) // "⏰ Every…" card above the box
  const ta = useRef(null)
  const { t: tr } = useTranslation('chats')
  const uploading = uploads.items.some((x) => !x.att)
  const ready = uploads.items.filter((x) => x.att)
  const canSend = (text.trim() || ready.length) && !uploading
  const send = async () => {
    let t = text.trim()
    if (!canSend) return
    const ids = ready.map((x) => x.att.id)
    setText('')
    setPick(null)
    uploads.clear()
    try {
      await api(`/api/threads/${threadId}/messages`, { method: 'POST', body: { text: t, attachments: ids } })
    } catch (e) {
      toast(tr('common:errors.sendFailed', { msg: e.message }))
      setText(t)
    }
  }
  // files pasted from the clipboard, or a very long paste → attach as a file
  const onPaste = (e) => {
    const files = [...(e.clipboardData?.files || [])]
    if (files.length) {
      e.preventDefault()
      uploads.add(files)
      return
    }
    const pasted = e.clipboardData?.getData('text') || ''
    if (pasted.length > LONG_PASTE) {
      e.preventDefault()
      uploads.add([pastedFile(pasted)])
      toast(tr('composer.longPaste', { n: pasted.length.toLocaleString() }))
    }
  }
  useEffect(() => {
    const el = ta.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 180) + 'px'
  }, [text])

  // "@" opens a picker of everyone in this chat (+ @all for the whole team)
  const options = pick
    ? [
        ...(members.length > 1 ? [{ id: 'all', name: 'all', emoji: '👥', color: '#FFB38A', role: tr('composer.allRole') }] : []),
        ...members,
      ].filter((m) => m.name.toLowerCase().startsWith(pick.query.toLowerCase()))
    : []
  const onChange = (e) => {
    const v = e.target.value
    setText(v)
    const caret = e.target.selectionStart
    const m = /(^|\s)@([\w-]*)$/.exec(v.slice(0, caret))
    setPick(m ? { query: m[2], start: caret - m[2].length - 1, index: 0 } : null)
  }
  const choose = (m) => {
    const before = text.slice(0, pick.start)
    const after = text.slice(pick.start + 1 + pick.query.length)
    const next = `${before}@${m.name} ${after.replace(/^\s+/, '')}`
    setText(next)
    setPick(null)
    requestAnimationFrame(() => {
      const el = ta.current
      if (!el) return
      el.focus()
      const pos = before.length + m.name.length + 2
      el.setSelectionRange(pos, pos)
    })
  }
  const onKeyDown = (e) => {
    if (pick && options.length) {
      if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
        e.preventDefault()
        const d = e.key === 'ArrowDown' ? 1 : -1
        setPick({ ...pick, index: (pick.index + d + options.length) % options.length })
        return
      }
      if ((e.key === 'Enter' || e.key === 'Tab') && !e.nativeEvent.isComposing) {
        e.preventDefault()
        choose(options[pick.index] || options[0])
        return
      }
      if (e.key === 'Escape') {
        setPick(null)
        return
      }
    }
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      e.preventDefault()
      send()
    }
  }
  return (
    <footer className="composer">
      {pick && options.length > 0 && (
        <div className="mention-pop">
          {options.map((m, i) => (
            <button key={m.id} className={`mention-opt ${i === pick.index ? 'on' : ''}`}
              onMouseEnter={() => setPick({ ...pick, index: i })}
              onMouseDown={(e) => { e.preventDefault(); choose(m) }}>
              {m.id === 'all'
                ? <span className="mention-all">👥</span>
                : <Mascot color={m.color} animal={animalFor(m)} emoji={m.emoji} status={m.status} size={28} bubble={false} />}
              <span className="grow">
                <b>@{m.name}</b>
                <small className="muted ellipsis">{m.role}</small>
              </span>
            </button>
          ))}
        </div>
      )}
      {routine && <RoutineCard agents={members} threadId={threadId} onClose={() => setRoutine(false)} />}
      {uploads.items.length > 0 && (
        <div className="pending-files">
          {uploads.items.map((x) => (
            <PendingFile key={x.key} x={x} onRemove={() => uploads.remove(x.key)} />
          ))}
        </div>
      )}
      <div className="compose-row">
        <PlusMenu open={plus} setOpen={setPlus} onFiles={pickFiles} onRoutine={() => setRoutine(true)} />
        <textarea
          onPaste={onPaste}
          ref={ta}
          rows={1}
          value={text}
          placeholder={busy ? tr('composer.placeholderBusy') : group ? tr('composer.placeholderGroup') : tr('composer.placeholderDm')}
          onChange={onChange}
          onKeyDown={onKeyDown}
          onBlur={() => setTimeout(() => setPick(null), 150)}
        />
        {busy && (
          <button className="icon-btn stop" title={tr('composer.stop')} onClick={() => api(`/api/threads/${threadId}/stop`, { method: 'POST' })}><Square size={14} fill="currentColor" /></button>
        )}
        <button className="send" disabled={!canSend} onClick={send} aria-label={tr('composer.send')}><ArrowUp size={20} strokeWidth={2.6} /></button>
      </div>
    </footer>
  )
}

// ＋ → attach files, or set up something that happens every day / week / month
function PlusMenu({ open, setOpen, onFiles, onRoutine }) {
  const { t } = useTranslation('chats')
  const ref = useRef(null)
  useEffect(() => {
    if (!open) return
    const off = (e) => !ref.current?.contains(e.target) && setOpen(false)
    const esc = (e) => e.key === 'Escape' && setOpen(false)
    document.addEventListener('pointerdown', off)
    document.addEventListener('keydown', esc)
    return () => {
      document.removeEventListener('pointerdown', off)
      document.removeEventListener('keydown', esc)
    }
  }, [open, setOpen])
  const pickItem = (f) => () => {
    setOpen(false)
    f()
  }
  return (
    <div className="plus-wrap" ref={ref}>
      <button className="attach-btn" onClick={() => setOpen(!open)} title={t('composer.plusTitle')} aria-label={t('composer.plusTitle')} aria-expanded={open}>
        <Plus size={20} strokeWidth={2.6} />
      </button>
      {open && (
        <div className="plus-menu" role="menu">
          <button role="menuitem" onClick={pickItem(onFiles)}>
            <span className="pm-ico">📎</span>
            <span>{t('composer.attach')}<small>{t('composer.attachSub')}</small></span>
          </button>
          <button role="menuitem" onClick={pickItem(onRoutine)}>
            <span className="pm-ico">⏰</span>
            <span>{t('composer.every')}<small>{t('composer.everySub')}</small></span>
          </button>
        </div>
      )}
    </div>
  )
}

function PendingFile({ x, onRemove }) {
  const { t } = useTranslation('chats')
  const status = !x.att ? `${Math.round(x.progress * 100)}%` : x.att.status === 'parsing' ? t('composer.reading') : x.att.status === 'error' ? t('composer.stored') : x.att.summary || fmtSize(x.size)
  return (
    <div className={`pending-file ${x.att ? 'done' : ''}`} style={{ '--p': x.progress }}>
      <FileIcon name={x.name} size={16} />
      <div className="grow">
        <b className="ellipsis">{x.name}</b>
        <small className="ellipsis">{(!x.att || x.att.status === 'parsing') && <Loader2 size={11} className="spin" />} {status}</small>
      </div>
      <button className="pf-x" onClick={onRemove} aria-label={t('composer.remove')}><X size={14} strokeWidth={3} /></button>
      {!x.att && <i className="pf-bar" />}
    </div>
  )
}

// every file handed over in this chat, in one place
function FilesButton({ threadId }) {
  const [open, setOpen] = useState(false)
  const [files, setFiles] = useState(null)
  const { t } = useTranslation('chats')
  useEffect(() => {
    if (!open) return
    Promise.all([
      api(`/api/deliverables?thread=${threadId}`).catch(() => []),
      api(`/api/threads/${threadId}/files`).catch(() => ({ files: [] })),
      api(`/api/uploads?thread=${threadId}`).catch(() => ({ uploads: [] })),
    ]).then(([d, w, u]) => {
      const deliv = (Array.isArray(d) ? d : d.deliverables || []).filter((x) => x.kind !== 'receipt')
      const delivNames = new Set(deliv.map((x) => x.title))
      setFiles({ deliv, work: (w.files || []).filter((x) => !delivNames.has(x.title)), up: u.uploads || [] })
    })
  }, [open, threadId])
  const empty = files && !files.deliv.length && !files.work.length && !files.up.length
  return (
    <>
      <button className="btn ghost sm" onClick={() => setOpen(true)} title={t('files.title')}>
        <Paperclip size={15} /> <span className="hide-sm">{t('files.button')}</span>
      </button>
      {open && createPortal(
        <div className="modal-bg" onClick={() => setOpen(false)}>
          <div className="modal files-modal" onClick={(e) => e.stopPropagation()}>
            <div className="row between">
              <h2>{t('files.title')}</h2>
              <button className="icon-btn" onClick={() => setOpen(false)} aria-label={t('common:close')}>✕</button>
            </div>
            {!files && <p className="muted">{t('common:loading')}</p>}
            {empty && <p className="muted">{t('files.empty')}</p>}
            {files?.deliv.length > 0 && <FileGroup title={t('files.delivered')} items={files.deliv.map((d) => ({ ...d, url: `/api/deliverables/${d.id}/file`, preview_url: `/api/deliverables/${d.id}/preview` }))} />}
            {files?.work.length > 0 && <FileGroup title={t('files.made')} items={files.work} />}
            {files?.up.length > 0 && <FileGroup title={t('files.sent')} items={files.up} />}
          </div>
        </div>,
        document.body,
      )}
    </>
  )
}

function FileGroup({ title, items }) {
  return (
    <section className="file-group">
      <h3>{title} <span className="pill">{items.length}</span></h3>
      <div className="file-grid">{items.map((d) => <DeliverableCard key={d.id} d={d} />)}</div>
    </section>
  )
}
