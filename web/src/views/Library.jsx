// Every real deliverable an agent has produced — reports, sheets, decks, images, apps
// and receipts — as one browsable, searchable grid. Complements the Pages tab.
import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, useStore } from '../store'
import Mascot, { animalFor } from '../components/Mascot'
import DeliverableCard, { KIND_META } from '../components/Deliverable'
import '../components/Deliverable.css'
import './Library.css'

// [kind, icon, library.json chips.<key>]
const CHIPS = [
  ['', '🗂️', 'all'],
  ['document', '📄', 'document'],
  ['spreadsheet', '📊', 'spreadsheet'],
  ['slides', '📽️', 'slides'],
  ['image', '🖼️', 'image'],
  ['app', '✨', 'app'],
  ['receipt', '🧾', 'receipt'],
]

function dayLabel(ts, t, lang) {
  const d = new Date(ts * 1000)
  const today = new Date()
  const yest = new Date(today - 86400000)
  const same = (a, b) => a.toDateString() === b.toDateString()
  if (same(d, today)) return t('today')
  if (same(d, yest)) return t('yesterday')
  return d.toLocaleDateString(lang === 'zh' ? 'zh-CN' : [], { weekday: 'long', month: 'short', day: 'numeric' })
}

export default function Library() {
  const { t, i18n } = useTranslation('library')
  const { agents } = useStore()
  const [items, setItems] = useState(null)
  const [kind, setKind] = useState('')
  const [agentId, setAgentId] = useState('')
  const [q, setQ] = useState('')

  const load = () => {
    const params = new URLSearchParams()
    if (kind) params.set('kind', kind)
    if (agentId) params.set('agent', agentId)
    api(`/api/deliverables?${params.toString()}`).then(setItems)
  }
  useEffect(load, [kind, agentId])

  useEffect(() => {
    const onNew = () => load()
    window.addEventListener('dot:deliverable', onNew)
    return () => window.removeEventListener('dot:deliverable', onNew)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, agentId])

  const filtered = useMemo(() => {
    const term = q.trim().toLowerCase()
    if (!term) return items || []
    return (items || []).filter((d) => (d.title || '').toLowerCase().includes(term))
  }, [items, q])

  const groups = useMemo(() => {
    const byDay = new Map()
    for (const d of filtered) {
      const label = dayLabel(d.created, t, i18n.language)
      if (!byDay.has(label)) byDay.set(label, [])
      byDay.get(label).push(d)
    }
    return byDay
  }, [filtered, t, i18n.language])

  return (
    <div>
      <header className="page-head">
        <div>
          <h1>{t('title')}</h1>
          <p className="muted">{t('lead')}</p>
        </div>
      </header>

      <div className="lib-toolbar">
        {CHIPS.map(([k, icon, key]) => (
          <button key={k || 'all'} className={`chip ${kind === k ? 'on' : ''}`} onClick={() => setKind(k)}>
            {icon} {t(`chips.${key}`)}
          </button>
        ))}
      </div>
      <div className="lib-toolbar">
        <input
          className="input lib-search"
          placeholder={t('search')}
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <select className="input" value={agentId} onChange={(e) => setAgentId(e.target.value)} style={{ maxWidth: 180 }}>
          <option value="">{t('allAgents')}</option>
          {agents.map((a) => (
            <option key={a.id} value={a.id}>{a.emoji} {a.name}</option>
          ))}
        </select>
      </div>

      {items === null && <div className="muted small">{t('common:loading')}</div>}
      {items !== null && filtered.length === 0 && (
        <div className="empty-card">
          <Mascot animal="fox" color="#FFB38A" status="idle" size={64} />
          <p className="muted">
            {t('empty')}
          </p>
        </div>
      )}
      {[...groups.entries()].map(([label, rows]) => (
        <section key={label} className="section">
          <div className="lib-day">{label}</div>
          <div className="lib-grid">
            {rows.map((d) => (
              <LibraryCard key={d.id} d={d} agent={agents.find((a) => a.id === d.agent_id)} />
            ))}
          </div>
        </section>
      ))}
    </div>
  )
}

function LibraryCard({ d, agent }) {
  const { t } = useTranslation()
  const label = t(`files:kind.${KIND_META[d.kind] ? d.kind : 'file'}`)
  return (
    <div className="col gap6">
      <DeliverableCard d={d} />
      {agent && (
        <small className="muted lib-agent-chip">
          <Mascot color={agent.color} animal={animalFor(agent)} emoji={agent.emoji} size={16} bubble={false} /> {agent.name} · {label}
        </small>
      )}
    </div>
  )
}
