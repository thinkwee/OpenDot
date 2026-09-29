import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { Camera, ImageUp, Link2, Loader2, Plus, Users, X } from 'lucide-react'
import { api, store, useStore, toast } from '../store'
import Mascot, { animalFor } from '../components/Mascot'
import { go } from '../App'
import { ago } from '../util'
import './soul.css'

export function AgentStack({ members, agents, size = 40 }) {
  const list = members.map((id) => agents.find((a) => a.id === id)).filter(Boolean)
  if (list.length === 1) return <Mascot color={list[0].color} animal={animalFor(list[0])} status={list[0].status} size={size} />
  return (
    <div className="stack" style={{ width: size, height: size }}>
      {list.slice(0, 4).map((a, i) => (
        <div key={a.id} className={`stack-i s${i}`}>
          <Mascot color={a.color} animal={animalFor(a)} status={a.status} size={size * 0.56} bubble={false} />
        </div>
      ))}
    </div>
  )
}

// Home: everyone you've got taking care of something, like a contact list.
export default function ChatList({ active }) {
  const { threads, agents, running } = useStore()
  const [modal, setModal] = useState(null)
  const { t: tr } = useTranslation('chats')

  return (
    <aside className="list">
      <header className="list-head">
        <h1>{tr('list.title')}</h1>
        <button className="btn sm new-agent-btn" onClick={() => setModal('agent')} title={tr('list.newTitle')}>
          <Plus size={16} strokeWidth={3} /> {tr('list.new')}
        </button>
      </header>
      <div className="list-body">
        {threads.map((t) => {
          const busy = t.members.some((id) => running[id]?.thread_id === t.id)
          const lead = agents.find((a) => a.id === t.members[0])
          const who = t.last?.role === 'agent' && t.kind === 'group' ? agents.find((a) => a.id === t.last.agent_id)?.name : null
          const line = busy ? null : t.last ? `${t.last.role === 'user' ? tr('list.you') : who ? who + ': ' : ''}${t.last.text}` : t.kind === 'group' ? tr('list.agentsCount', { count: t.members.length }) : lead?.responsibility || lead?.tagline || lead?.role
          return (
            <button key={t.id} className={`thread ${active === t.id ? 'on' : ''} ${t.needs_you ? 'needs' : ''}`} onClick={() => go('chats', t.id)}>
              <AgentStack members={t.members} agents={agents} size={46} />
              <div className="thread-txt">
                <div className="row between">
                  <b>{t.title}</b>
                  <small className="muted">{ago(t.last?.at || t.updated)}</small>
                </div>
                <small className="muted ellipsis">
                  {busy ? <span className="typing-txt">{tr('list.workingOnIt')}</span> : line}
                </small>
              </div>
              {t.needs_you && <span className="needs-dot" title={tr('list.waiting')}>1</span>}
            </button>
          )
        })}
        <button className="thread add-row" onClick={() => setModal('agent')}>
          <span className="add-ico"><Plus size={22} strokeWidth={3} /></span>
          <div className="thread-txt">
            <b>{tr('list.someoneNew')}</b>
            <small className="muted">{tr('list.someoneNewSub')}</small>
          </div>
        </button>
      </div>
      {modal === 'group' && <NewGroup onClose={() => setModal(null)} agents={agents} />}
      {modal === 'agent' && <NewAgent onClose={() => setModal(null)} onGroup={() => setModal('group')} />}
    </aside>
  )
}

function NewGroup({ onClose, agents }) {
  const [sel, setSel] = useState(agents.slice(0, 2).map((a) => a.id))
  const [title, setTitle] = useState('')
  const { t } = useTranslation('chats')
  const create = async () => {
    const th = await api('/api/threads', { method: 'POST', body: { title: title || t('group.defaultTitle'), members: sel } })
    store.set((s) => ({ threads: [th, ...s.threads.filter((x) => x.id !== th.id)] }))
    onClose()
    go('chats', th.id)
  }
  return createPortal(
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <h2>{t('group.title')}</h2>
        <p className="muted">{t('group.desc')}</p>
        <input className="input" placeholder={t('group.placeholder')} value={title} onChange={(e) => setTitle(e.target.value)} />
        <div className="pick-grid">
          {agents.map((a) => {
            const on = sel.includes(a.id)
            return (
              <button key={a.id} className={`pick ${on ? 'on' : ''}`} onClick={() => setSel(on ? sel.filter((x) => x !== a.id) : [...sel, a.id])}>
                <Mascot color={a.color} animal={animalFor(a)} size={44} />
                <b>{a.name}</b>
                {on && <span className="pick-n">{sel.indexOf(a.id) + 1}</span>}
              </button>
            )
          })}
        </div>
        <div className="row end gap8">
          <button className="btn ghost" onClick={onClose}>{t('common:cancel')}</button>
          <button className="btn" disabled={sel.length < 1} onClick={create}>{t('group.start')}</button>
        </div>
      </div>
    </div>,
    document.body,
  )
}

// example requests shown as the placeholder — see chats.json `ideas`

// "Who do you need?" — one sentence, a ready-made one, or a place's link / QR code.
export function NewAgent({ onClose, onGroup }) {
  const [text, setText] = useState('')
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState('')
  const [templates, setTemplates] = useState([])
  const [scan, setScan] = useState(false)
  const fileRef = useRef(null)
  const { t } = useTranslation('chats')
  const ideas = t('ideas', { returnObjects: true })
  useEffect(() => {
    api('/api/agents/templates').then(setTemplates).catch(() => {})
  }, [])

  const hire = async (body, label) => {
    setBusy(label)
    try {
      const r = await api('/api/agents/hire', { method: 'POST', body })
      store.set((s) => ({
        agents: s.agents.some((a) => a.id === r.agent.id) ? s.agents : [...s.agents, r.agent],
        threads: s.threads.some((t) => t.id === r.thread.id) ? s.threads : [r.thread, ...s.threads],
      }))
      onClose()
      go('chats', r.thread.id)
    } catch (e) {
      toast(t('common:errors.generic', { msg: e.message.slice(0, 120) }))
      setBusy('')
    }
  }
  const fromQr = (data) => {
    setScan(false)
    if (/^https?:\/\//i.test(data)) hire({ url: data, origin: 'qr' }, 'qr')
    else hire({ text: data, origin: 'qr' }, 'qr')
  }
  const pickImage = async (f) => {
    if (!f) return
    try {
      const QrScanner = (await import('qr-scanner')).default
      const r = await QrScanner.scanImage(f, { returnDetailedScanResult: true })
      fromQr(r.data)
    } catch {
      toast(t('hire.noQr'))
    }
  }

  return createPortal(
    <div className="modal-bg" onClick={() => !busy && onClose()}>
      <div className="modal hire" onClick={(e) => e.stopPropagation()}>
        <div className="row between">
          <h2>{t('hire.title')}</h2>
          <button className="icon-btn" onClick={onClose} aria-label={t('common:close')}><X size={18} /></button>
        </div>
        {busy ? (
          <div className="hire-busy">
            <Mascot status="working" size={88} animal="fox" color="#FFB38A" />
            <b>{busy === 'qr' || busy === 'link' ? t('hire.readingPlace') : t('hire.settingUp')}</b>
            <small className="muted">{t('hire.settingUpSub')}</small>
          </div>
        ) : (
          <>
            <p className="muted">{t('hire.intro')} <a href="#/examples" onClick={() => onClose()}>💡 {t('hire.ideas')}</a></p>
            <div className="hire-say">
              <textarea
                className="input"
                rows={2}
                autoFocus
                value={text}
                placeholder={ideas[Math.floor(Date.now() / 60000) % ideas.length]}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing && text.trim()) {
                    e.preventDefault()
                    hire({ text }, 'text')
                  }
                }}
              />
              <button className="btn" disabled={!text.trim()} onClick={() => hire({ text }, 'text')}>{t('hire.make')}</button>
            </div>

            <h4 className="hire-h">{t('hire.orPick')}</h4>
            <div className="tpl-grid">
              {templates.map((t) => (
                <button key={t.key} className="tpl" style={{ '--c': t.color }} onClick={() => hire({ template: t.key }, 'template')}>
                  <Mascot color={t.color} animal={t.avatar} size={46} bubble={false} />
                  <b>{t.name}</b>
                  <small>{t.role}</small>
                </button>
              ))}
            </div>

            <h4 className="hire-h">{t('hire.fromPlace')}</h4>
            <p className="muted small">{t('hire.fromPlaceDesc')}</p>
            <div className="hire-place">
              <button className="btn ghost" onClick={() => setScan(true)}><Camera size={16} /> {t('hire.scan')}</button>
              <button className="btn ghost" onClick={() => fileRef.current?.click()}><ImageUp size={16} /> {t('hire.fromPicture')}</button>
              <input ref={fileRef} type="file" accept="image/*" hidden onChange={(e) => pickImage(e.target.files?.[0])} />
              <div className="hire-link">
                <Link2 size={16} />
                <input className="input" placeholder={t('hire.pasteLink')} value={url} onChange={(e) => setUrl(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && url.trim() && hire({ url: url.trim() }, 'link')} />
                {url.trim() && <button className="btn sm" onClick={() => hire({ url: url.trim() }, 'link')}>{t('hire.go')}</button>}
              </div>
            </div>
            {onGroup && (
              <button className="hire-group" onClick={onGroup}><Users size={15} /> {t('hire.groupLink')}</button>
            )}
          </>
        )}
        {scan && <Scanner onResult={fromQr} onClose={() => setScan(false)} />}
      </div>
    </div>,
    document.body,
  )
}

function Scanner({ onResult, onClose }) {
  const video = useRef(null)
  const [err, setErr] = useState('')
  const { t } = useTranslation('chats')
  useEffect(() => {
    let scanner
    let done = false
    import('qr-scanner').then(({ default: QrScanner }) => {
      if (!video.current) return
      scanner = new QrScanner(video.current, (r) => {
        if (done) return
        done = true
        onResult(r.data)
      }, { returnDetailedScanResult: true, highlightScanRegion: true, preferredCamera: 'environment' })
      scanner.start().catch(() => setErr(t('scanner.noCamera')))
    })
    return () => scanner?.destroy()
  }, [])
  return (
    <div className="scanner">
      <video ref={video} playsInline muted />
      {err ? <p className="scan-err">{err}</p> : <p className="scan-tip"><Loader2 size={14} className="spin" /> {t('scanner.point')}</p>}
      <button className="btn" onClick={onClose}>{t('common:close')}</button>
    </div>
  )
}
