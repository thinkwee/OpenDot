import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, useStore } from '../store'
import Mascot, { animalFor } from '../components/Mascot'
import { go } from '../App'
import './Usage.css'

// Settings → Usage: how many tokens your agents used, on what, and roughly what it cost.
const RANGES = ['today', '7d', '30d', 'all']

export default function Usage() {
  const { t, i18n } = useTranslation('usage')
  const [range, setRange] = useState(() => { try { return localStorage.getItem('dot_usage_range') || '7d' } catch { return '7d' } })
  const [d, setD] = useState(null)
  useEffect(() => {
    let off = false
    const load = () => api(`/api/usage?range=${range}`).then((x) => !off && setD(x)).catch(() => {})
    load()
    const id = setInterval(load, 30000)
    try { localStorage.setItem('dot_usage_range', range) } catch { /* private mode */ }
    return () => { off = true; clearInterval(id) }
  }, [range])
  const f = useMemo(() => fmt(i18n.language), [i18n.language])

  return (
    <div className="usage">
      <div className="usage-top">
        <div className="seg usage-range" role="tablist">
          {RANGES.map((r) => (
            <button key={r} role="tab" aria-selected={range === r} className={range === r ? 'on' : ''} onClick={() => setRange(r)}>{t(`range.${r}`)}</button>
          ))}
        </div>
        {d?.since && <span className="muted small">{t('since', { date: new Date(d.since * 1000).toLocaleDateString(i18n.language) })}</span>}
      </div>
      {!d ? <div className="card muted">…</div> : !d.totals.calls ? <Empty since={d.since} /> : (
        <>
          <Tiles d={d} f={f} />
          <Chart d={d} f={f} />
          <div className="cards usage-cards">
            <ByAgent rows={d.agents} f={f} />
            <ByKind rows={d.kinds} f={f} />
            <ByModel rows={d.models} f={f} />
            <ByChat rows={d.threads} f={f} />
          </div>
          <p className="muted small usage-note">{t('note')}</p>
        </>
      )}
    </div>
  )
}

function Empty({ since }) {
  const { t } = useTranslation('usage')
  return (
    <div className="card usage-empty">
      <b>{since ? t('empty.range') : t('empty.title')}</b>
      <span className="muted">{since ? t('empty.rangeBody') : t('empty.body')}</span>
    </div>
  )
}

// ---------------- the numbers you look at first ----------------
function Tiles({ d, f }) {
  const { t } = useTranslation('usage')
  const x = d.totals
  const total = x.input + x.output
  const prev = d.prev ? d.prev.input + d.prev.output : null
  const change = prev ? (total - prev) / prev : null
  const cachedShare = x.input ? x.cached / x.input : 0
  return (
    <div className="usage-tiles">
      <div className="card usage-tile hero">
        <span className="usage-label">{t('tile.total')}</span>
        <b className="usage-value">{f.num(total)}</b>
        <span className="muted small">
          {change === null ? t('tile.calls', { count: x.calls, n: f.num(x.calls) })
            : <>{change >= 0 ? '▲' : '▼'} {f.pct(Math.abs(change))} {t(`tile.vs.${d.range}`)} · {t('tile.calls', { count: x.calls, n: f.num(x.calls) })}</>}
        </span>
      </div>
      <div className="card usage-tile">
        <span className="usage-label"><i className="usage-key in" />{t('input')}</span>
        <b className="usage-value">{f.num(x.input)}</b>
        <span className="muted small">{t('tile.perCall', { n: f.num(x.avg_input) })}{x.cached ? ` · ${t('tile.cached', { pct: f.pct(cachedShare) })}` : ''}</span>
      </div>
      <div className="card usage-tile">
        <span className="usage-label"><i className="usage-key out" />{t('output')}</span>
        <b className="usage-value">{f.num(x.output)}</b>
        <span className="muted small">{x.reasoning ? t('tile.reasoning', { n: f.num(x.reasoning) }) : t('tile.outputHint')}</span>
      </div>
      <div className="card usage-tile">
        <span className="usage-label">{t('tile.cost')}</span>
        <b className="usage-value">{x.cost == null ? '—' : f.money(x.cost)}</b>
        <span className="muted small">{x.unpriced ? t('tile.unpriced', { count: x.unpriced, n: f.num(x.unpriced) }) : t('tile.costHint')}</span>
      </div>
    </div>
  )
}

// ---------------- tokens over time: input and output, stacked ----------------
const H = 190
const PAD = { l: 46, r: 8, t: 10, b: 26 }

function Chart({ d, f }) {
  const { t, i18n } = useTranslation('usage')
  const box = useRef(null)
  const [w, setW] = useState(640)
  const [hover, setHover] = useState(null)
  const [table, setTable] = useState(false)
  useLayoutEffect(() => {
    const el = box.current
    if (!el) return
    const ro = new ResizeObserver(([e]) => setW(Math.max(260, e.contentRect.width)))
    ro.observe(el)
    return () => ro.disconnect()
  }, [table])
  const b = d.buckets
  const max = Math.max(1, ...b.map((x) => x.input + x.output))
  const ticks = niceTicks(max)
  const top = ticks[ticks.length - 1]
  const iw = w - PAD.l - PAD.r
  const ih = H - PAD.t - PAD.b
  const slot = iw / b.length
  const bw = Math.max(2, Math.min(24, slot * 0.64))
  const y = (v) => PAD.t + ih - (v / top) * ih
  const label = (x, long) => when(x.start, d.unit, i18n.language, long)
  const every = Math.ceil(b.length / Math.max(2, Math.floor(iw / 64)))

  return (
    <div className="card usage-chart">
      <div className="usage-chart-head">
        <div>
          <h3>{t('chart.title')}</h3>
          <div className="usage-legend">
            <span><i className="usage-key in" />{t('input')}</span>
            <span><i className="usage-key out" />{t('output')}</span>
          </div>
        </div>
        <button className="btn ghost sm" onClick={() => setTable(!table)}>{table ? t('chart.showChart') : t('chart.showTable')}</button>
      </div>
      {table ? (
        <div className="usage-table-wrap">
          <table className="usage-table">
            <thead><tr><th>{t(`chart.${d.unit}`)}</th><th>{t('input')}</th><th>{t('output')}</th><th>{t('calls')}</th><th>{t('cost')}</th></tr></thead>
            <tbody>
              {b.filter((x) => x.calls).map((x) => (
                <tr key={x.start}><td>{label(x, true)}</td><td>{f.full(x.input)}</td><td>{f.full(x.output)}</td><td>{f.full(x.calls)}</td><td>{x.cost ? f.money(x.cost) : '—'}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="usage-plot" ref={box} onMouseLeave={() => setHover(null)}>
          <svg width={w} height={H} role="img" aria-label={t('chart.title')}>
            {ticks.map((v) => (
              <g key={v}>
                <line x1={PAD.l} x2={w - PAD.r} y1={y(v)} y2={y(v)} className="usage-grid" />
                <text x={PAD.l - 8} y={y(v)} dy="0.32em" textAnchor="end" className="usage-axis">{f.num(v)}</text>
              </g>
            ))}
            {b.map((x, i) => {
              const cx = PAD.l + slot * i + slot / 2
              const hIn = (x.input / top) * ih
              const hOut = (x.output / top) * ih
              const base = PAD.t + ih
              const gap = x.input && x.output ? 2 : 0
              return (
                <g key={x.start} className={hover === i ? 'on' : ''}>
                  {x.input > 0 && <path className="usage-bar in" d={bar(cx - bw / 2, base - hIn, bw, hIn, !x.output)} />}
                  {x.output > 0 && <path className="usage-bar out" d={bar(cx - bw / 2, base - hIn - gap - hOut, bw, hOut, true)} />}
                  {i % every === 0 && <text x={cx} y={H - 8} textAnchor="middle" className="usage-axis">{label(x)}</text>}
                  <rect x={PAD.l + slot * i} y={PAD.t} width={slot} height={ih} fill="transparent"
                    onMouseEnter={() => setHover(i)} onClick={() => setHover(i)} />
                </g>
              )
            })}
            <line x1={PAD.l} x2={w - PAD.r} y1={PAD.t + ih} y2={PAD.t + ih} className="usage-base" />
          </svg>
          {hover !== null && (() => {
            const x = b[hover]
            const cx = PAD.l + slot * hover + slot / 2
            return (
              <div className="usage-tip" style={{ left: Math.min(Math.max(cx, 90), w - 90), top: Math.max(4, y(x.input + x.output) - 12) }}>
                <b>{label(x, true)}</b>
                <span><i className="usage-key in" />{t('input')} <em>{f.full(x.input)}</em></span>
                <span><i className="usage-key out" />{t('output')} <em>{f.full(x.output)}</em></span>
                <span className="muted">{t('tile.calls', { count: x.calls, n: f.num(x.calls) })}{x.cost ? ` · ${f.money(x.cost)}` : ''}</span>
              </div>
            )
          })()}
        </div>
      )}
    </div>
  )
}

// a column segment with a 4px rounded end on top, square at the baseline
function bar(x, y, w, h, round) {
  const r = round ? Math.min(4, h, w / 2) : 0
  return `M${x},${y + h} V${y + r} Q${x},${y} ${x + r},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h} Z`
}

function niceTicks(max) {
  const raw = max / 4
  const mag = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || raw
  return [0, 1, 2, 3, 4].map((i) => i * step).filter((v, i) => i === 0 || v - step < max)
}

function when(ts, unit, lang, long) {
  const d = new Date(ts * 1000)
  if (unit === 'hour') return d.toLocaleTimeString(lang, { hour: '2-digit', minute: '2-digit' })
  const opts = long ? { weekday: 'short', month: 'short', day: 'numeric' } : { month: 'numeric', day: 'numeric' }
  return (unit === 'week' && long ? '≥ ' : '') + d.toLocaleDateString(lang, opts)
}

// ---------------- breakdowns ----------------
function Rows({ rows, f, render, color }) {
  const top = Math.max(1, ...rows.map((r) => r.input + r.output))
  return rows.map((r, i) => (
    <div key={i} className="usage-row">
      <div className="usage-row-head">
        {render(r)}
        <span className="usage-row-num"><b>{f.num(r.input + r.output)}</b>{r.cost ? <small className="muted"> · {f.money(r.cost)}</small> : null}</span>
      </div>
      <div className="usage-track"><i style={{ width: `${Math.max(1.5, ((r.input + r.output) / top) * 100)}%`, background: color?.(r) }} /></div>
    </div>
  ))
}

function ByAgent({ rows, f }) {
  const { t } = useTranslation('usage')
  const { agents } = useStore()
  const by = Object.fromEntries(agents.map((a) => [a.id, a]))
  return (
    <div className="card">
      <h3>{t('by.agent')}</h3>
      <Rows rows={rows} f={f} color={(r) => by[r.agent_id]?.color}
        render={(r) => {
          const a = by[r.agent_id]
          return a ? (
            <button className="usage-who" onClick={() => go('team', a.id)}>
              <Mascot color={a.color} animal={animalFor(a)} size={24} bubble={false} /> {a.name}
            </button>
          ) : <span className="usage-who muted">{r.agent_id ? t('by.gone') : t('by.nobody')}</span>
        }} />
    </div>
  )
}

function ByKind({ rows, f }) {
  const { t } = useTranslation('usage')
  return (
    <div className="card">
      <h3>{t('by.kind')}</h3>
      <Rows rows={rows} f={f} render={(r) => <span className="usage-who">{t(`kind.${r.kind}`, { defaultValue: r.kind })}</span>} />
    </div>
  )
}

function ByModel({ rows, f }) {
  const { t } = useTranslation('usage')
  return (
    <div className="card">
      <h3>{t('by.model')}</h3>
      <Rows rows={rows} f={f} render={(r) => (
        <span className="usage-who mono" title={r.model}>{r.model}{r.unpriced === r.calls ? <small className="muted"> · {t('by.noPrice')}</small> : null}</span>
      )} />
    </div>
  )
}

function ByChat({ rows, f }) {
  const { t } = useTranslation('usage')
  if (!rows.length) return null
  return (
    <div className="card">
      <h3>{t('by.chat')}</h3>
      <Rows rows={rows.map((r) => ({ ...r, input: r.tokens, output: 0 }))} f={f}
        render={(r) => <button className="usage-who" onClick={() => go('chats', r.thread_id)}>{r.title}</button>} />
    </div>
  )
}

// ---------------- numbers ----------------
function fmt(lang) {
  const compact = new Intl.NumberFormat(lang, { notation: 'compact', maximumFractionDigits: 1 })
  const whole = new Intl.NumberFormat(lang)
  return {
    num: (n) => (n < 10000 ? whole.format(n) : compact.format(n)),
    full: (n) => whole.format(n),
    pct: (x) => `${Math.round(x * 100)}%`,
    money: (x) => (x > 0 && x < 0.01 ? '< $0.01' : `$${x.toFixed(x < 10 ? 2 : 0)}`),
  }
}
