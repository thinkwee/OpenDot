import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { CheckCheck, MessageCircle, Trash2, X } from 'lucide-react'
import { api, store, toast, useStore } from '../store'
import Mascot, { animalFor } from '../components/Mascot'
import { ApprovalCard } from './ChatView'
import { go } from '../App'
import { ago, md } from '../util'

// [icon, label key in inbox.json]
const KIND = {
  report: ['📋', 'kind.report'], note: ['📝', 'kind.note'], email: ['✉️', 'kind.email'], sms: ['💬', 'kind.sms'],
  approval: ['🛡️', 'kind.approval'],
}
const sourceLabel = (title = '', t) => (title.split(' · ')[1] || '')
  .replace('automation:', '⏰ ').replace('heartbeat', t('src.heartbeat')).replace(/^event:/, '⚡ ')

export default function Inbox() {
  const { agents, approvals, inboxUnread, threads } = useStore()
  const [items, setItems] = useState(null)
  const [filter, setFilter] = useState('all')
  const [open, setOpen] = useState(null)
  const { t } = useTranslation('inbox')
  const load = () => api('/api/inbox').then(setItems).catch(() => setItems([]))
  useEffect(() => {
    load()
  }, [inboxUnread, approvals.length])

  const markRead = async (it) => {
    if (it.status !== 'unread') return
    setItems((xs) => xs.map((x) => (x.id === it.id ? { ...x, status: 'read' } : x)))
    store.set((s) => ({ inboxUnread: Math.max(0, s.inboxUnread - 1) }))
    try { await api(`/api/inbox/${it.id}`, { method: 'POST', body: { status: 'read' } }) } catch { /* next load fixes it */ }
  }
  const preview = (it) => { setOpen(it); markRead(it) }
  const readAll = async () => {
    try {
      await api('/api/inbox/read-all', { method: 'POST' })
      setItems((xs) => xs.map((x) => (x.status === 'unread' ? { ...x, status: 'read' } : x)))
      store.set({ inboxUnread: 0 })
      toast(t('caughtUp'))
    } catch (e) { toast('⚠️ ' + e.message) }
  }
  const clearRead = async () => {
    try {
      const r = await api('/api/inbox/clear-read', { method: 'POST' })
      toast(r.removed ? t('cleared', { count: r.removed }) : t('nothingToClear'))
      load()
    } catch (e) { toast('⚠️ ' + e.message) }
  }
  const remove = async (it) => {
    await api(`/api/inbox/${it.id}`, { method: 'DELETE' })
    setItems((xs) => xs.filter((x) => x.id !== it.id))
    setOpen(null)
  }

  const notes = (items || []).filter((i) => i.kind !== 'approval')
  const unread = notes.filter((i) => i.status === 'unread').length
  const shown = filter === 'unread' ? notes.filter((i) => i.status === 'unread') : notes

  return (
    <div>
      <header className="page-head">
        <div>
          <h1>{t('title')}</h1>
          <p className="muted">{t('subtitle')}</p>
        </div>
        <div className="row gap6">
          <button className="btn ghost sm" onClick={readAll} disabled={!unread}><CheckCheck size={15} /> {t('markAll')}</button>
          <button className="btn ghost sm" onClick={clearRead} disabled={!notes.some((i) => i.status !== 'unread')} title={t('clearReadTitle')}><Trash2 size={15} /> {t('clearRead')}</button>
        </div>
      </header>
      {approvals.length > 0 && (
        <section className="section">
          <h3>{t('needsOk')}</h3>
          {approvals.map((ap) => (
            <ApprovalCard key={ap.id} ap={ap} agent={agents.find((a) => a.id === ap.agent_id)} />
          ))}
        </section>
      )}
      <section className="section">
        <div className="row between mb8">
          <h3 style={{ margin: 0 }}>{t('updates')}</h3>
          <div className="seg inbox-seg">
            <button className={filter === 'all' ? 'on' : ''} onClick={() => setFilter('all')}>{t('all')} {notes.length || ''}</button>
            <button className={filter === 'unread' ? 'on' : ''} onClick={() => setFilter('unread')}>{t('unread')} {unread || ''}</button>
          </div>
        </div>
        {items && shown.length === 0 && (
          <div className="empty-card">
            <Mascot animal="fox" color="#FFB38A" status="sleeping" size={64} />
            <p className="muted">{filter === 'unread' && notes.length ? t('emptyUnread') : t('empty')}</p>
          </div>
        )}
        {shown.map((it) => {
          const a = agents.find((x) => x.id === it.agent_id)
          const [icon, label] = KIND[it.kind] || KIND.note
          return (
            <button key={it.id} className={`inbox-item ${it.status}`} onClick={() => preview(it)}>
              <Mascot color={a?.color} animal={a && animalFor(a)} emoji={a?.emoji} size={36} bubble={false} />
              <div className="grow">
                <div className="row between gap8">
                  <b className="ellipsis">{it.title}</b>
                  <small className="muted" style={{ flex: 'none' }}>{ago(it.created)}</small>
                </div>
                <small className="inbox-kind">{icon} {t(label)}</small>
                {it.body && <div className="inbox-body clamp" dangerouslySetInnerHTML={{ __html: md(it.body) }} />}
              </div>
              {it.status === 'unread' && <span className="unread-dot" />}
            </button>
          )
        })}
      </section>

      {open && createPortal(
        <InboxPreview it={open} agent={agents.find((x) => x.id === open.agent_id)}
          thread={threads.find((t) => t.id === open.thread_id)}
          onClose={() => setOpen(null)} onDelete={() => remove(open)} />,
        document.body,
      )}
    </div>
  )
}

function InboxPreview({ it, agent, thread, onClose, onDelete }) {
  useEffect(() => {
    const esc = (e) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [])
  const { t } = useTranslation('inbox')
  const [icon, label] = KIND[it.kind] || KIND.note
  const src = sourceLabel(it.title, t)
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal inbox-preview" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={it.title}>
        <header className="ip-head" style={{ '--c': agent?.color }}>
          <Mascot color={agent?.color} animal={agent && animalFor(agent)} emoji={agent?.emoji} size={48} bubble={false} />
          <div className="grow">
            <h2 className="ellipsis">{it.title}</h2>
            <div className="row gap6 wrap">
              <span className="pill">{icon} {t(label)}</span>
              {src && <span className="pill">{src}</span>}
              <small className="muted">{ago(it.created)}</small>
            </div>
          </div>
          <button className="icon-btn" onClick={onClose} aria-label={t('common:close')}><X size={20} /></button>
        </header>
        <div className="ip-body md" dangerouslySetInnerHTML={{ __html: md(it.body || t('noDetails')) }} />
        <footer className="row between gap6 wrap">
          <button className="btn danger sm" onClick={onDelete}><Trash2 size={14} /> {t('common:delete')}</button>
          <div className="row gap6">
            <button className="btn ghost sm" onClick={onClose}>{t('common:close')}</button>
            {it.thread_id && (
              <button className="btn sm" onClick={() => { onClose(); go('chats', it.thread_id) }}>
                <MessageCircle size={14} /> {thread ? t('openThread', { title: thread.title }) : t('openChat')}
              </button>
            )}
          </div>
        </footer>
      </div>
    </div>
  )
}
