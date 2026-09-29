// "Ideas to steal" — everyday things people hand to an agent. Tap one → a new agent,
// pre-filled with that sentence (same /api/agents/hire flow as the new-agent sheet).
import { useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { ChevronLeft, X } from 'lucide-react'
import { api, store, toast } from '../store'
import Mascot from '../components/Mascot'
import { go } from '../App'
import './Examples.css'

const CATS = ['all', 'home', 'papers', 'out', 'health', 'family', 'work', 'places']
const TINT = {
  home: 'var(--m-yellow)', papers: 'var(--m-blue)', out: 'var(--m-teal)', health: 'var(--m-pink)',
  family: 'var(--m-coral)', work: 'var(--m-lilac)', places: 'var(--m-mint)',
}

export default function Examples() {
  const { t } = useTranslation('examples')
  const [cat, setCat] = useState('all')
  const [pick, setPick] = useState(null) // the sentence being turned into an agent ('' = your own)
  const items = t('items', { returnObjects: true })
  // "All" deals the categories out like cards so neighbours differ
  const seen = {}
  const list = (Array.isArray(items) ? items : []).map((it) => [it, (seen[it[0]] = (seen[it[0]] || 0) + 1)])
  const shown = cat === 'all'
    ? list.sort((a, b) => a[1] - b[1] || CATS.indexOf(a[0][0]) - CATS.indexOf(b[0][0])).map(([it]) => it)
    : list.map(([it]) => it).filter(([c]) => c === cat)

  return (
    <div className="examples">
      <header className="page-head ex-head">
        <div>
          <button className="ex-back" onClick={() => history.length > 1 ? history.back() : go('chats')}><ChevronLeft size={16} /> {t('back')}</button>
          <h1>{t('title')}</h1>
          <p className="muted">{t('intro')}</p>
        </div>
      </header>

      <div className="ex-cats" role="tablist">
        {CATS.map((c) => (
          <button key={c} role="tab" aria-selected={cat === c} className={`chip ${cat === c ? 'on' : ''}`} onClick={() => setCat(c)}
            style={c !== 'all' ? { '--tint': TINT[c] } : null}>
            {c !== 'all' && <i className="ex-dot" />}{t(`cats.${c}`)}
          </button>
        ))}
      </div>

      <div className="ex-grid">
        {shown.map(([c, emoji, text], i) => (
          <button key={text} className={`ex-card r${i % 4}`} style={{ '--tint': TINT[c] }} onClick={() => setPick(text)}>
            <span className="ex-emoji" aria-hidden="true">{emoji}</span>
            <span className="ex-text">“{text}”</span>
            <small className="ex-tag">{t(`cats.${c}`)}</small>
          </button>
        ))}
      </div>

      <button className="ex-own" onClick={() => setPick('')}>{t('cta')}</button>
      {pick != null && <TrySheet initial={pick} onClose={() => setPick(null)} />}
    </div>
  )
}

function TrySheet({ initial, onClose }) {
  const { t } = useTranslation('examples')
  const [text, setText] = useState(initial)
  const [busy, setBusy] = useState(false)
  const make = async () => {
    if (!text.trim() || busy) return
    setBusy(true)
    try {
      const r = await api('/api/agents/hire', { method: 'POST', body: { text: text.trim() } })
      store.set((s) => ({
        agents: s.agents.some((a) => a.id === r.agent.id) ? s.agents : [...s.agents, r.agent],
        threads: s.threads.some((x) => x.id === r.thread.id) ? s.threads : [r.thread, ...s.threads],
      }))
      onClose()
      go('chats', r.thread.id)
    } catch (e) {
      toast(t('common:errors.generic', { msg: e.message.slice(0, 120) }))
      setBusy(false)
    }
  }
  return createPortal(
    <div className="modal-bg" onClick={() => !busy && onClose()}>
      <div className="modal hire ex-try" onClick={(e) => e.stopPropagation()}>
        <div className="row between">
          <h2>{t('try.title')}</h2>
          <button className="icon-btn" onClick={onClose} aria-label={t('common:close')} disabled={busy}><X size={18} /></button>
        </div>
        {busy ? (
          <div className="hire-busy">
            <Mascot status="working" size={88} animal="fox" color="#FFB38A" />
            <b>{t('try.settingUp')}</b>
            <small className="muted">{t('try.settingUpSub')}</small>
          </div>
        ) : (
          <>
            <p className="muted small">{t('try.hint')}</p>
            <textarea className="input" rows={3} autoFocus value={text} onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                  e.preventDefault()
                  make()
                }
              }} />
            <div className="row end gap8">
              <button className="btn ghost" onClick={onClose}>{t('common:cancel')}</button>
              <button className="btn" disabled={!text.trim()} onClick={make}>{t('try.make')}</button>
            </div>
          </>
        )}
      </div>
    </div>,
    document.body,
  )
}
