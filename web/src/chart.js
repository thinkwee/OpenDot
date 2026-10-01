// A ```chart block in a message becomes a small SVG chart, drawn right in the bubble.
// The spec:
//   {"type": "bar"|"line"|"pie", "title": "...", "labels": [...], "series": [{"name", "values": [...]}],
//    "y": {"min", "max"}}   (optional: the axis reaches at least this far)
// Plain markup (no script), so it goes through the same sanitiser as the rest of the message;
// pointing at it (or tapping, on a phone) shows the values there, and the numbers are one
// tap away as a table.

const W = 400, H = 210, L = 38, R = 8, T = 10, B = 28
const MAX_SERIES = 6

const esc = (s) => String(s ?? '').replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]))
const num = (v) => (typeof v === 'number' && isFinite(v) ? v : Number(v))
const fmt = (v) => (isFinite(v) ? new Intl.NumberFormat(undefined, { notation: Math.abs(v) >= 10000 ? 'compact' : 'standard', maximumFractionDigits: 2 }).format(v) : '–')
const short = (s, n = 12) => (s.length > n ? s.slice(0, n - 1) + '…' : s)

function niceTicks(lo, hi, n = 4) {
  if (lo === hi) hi = lo + 1
  const raw = (hi - lo) / n, mag = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw)
  const out = []
  for (let v = Math.floor(lo / step) * step; v <= hi + step * 1e-9; v += step) out.push(+v.toFixed(10))
  if (out[out.length - 1] < hi) out.push(out[out.length - 1] + step)
  return out
}

function parse(text) {
  let s
  try { s = JSON.parse(text) } catch { return null }
  const type = ['bar', 'line', 'pie'].includes(s?.type) ? s.type : 'bar'
  const labels = (Array.isArray(s?.labels) ? s.labels : []).slice(0, 60).map(String)
  const series = (Array.isArray(s?.series) ? s.series : []).slice(0, MAX_SERIES)
    .map((x, i) => ({ name: String(x?.name ?? `#${i + 1}`), values: labels.map((_, j) => num(x?.values?.[j])) }))
  if (!labels.length || !series.length) return null
  const y = { min: num(s.y?.min), max: num(s.y?.max) }  // values the axis must reach, e.g. a threshold
  return { type, title: s.title ? String(s.title) : '', labels, series, y }
}

function legend(names) {
  if (names.length < 2) return ''
  return `<div class="dc-legend">${names.map((n, i) => `<span><i class="dc-sw s${i}"></i>${esc(n)}</span>`).join('')}</div>`
}

function table({ labels, series }, label) {
  const head = `<tr><th></th>${series.map((s) => `<th>${esc(s.name)}</th>`).join('')}</tr>`
  const rows = labels.map((l, j) => `<tr><td>${esc(l)}</td>${series.map((s) => `<td>${fmt(s.values[j])}</td>`).join('')}</tr>`).join('')
  return `<details class="dc-data"><summary>${esc(label)}</summary><table>${head}${rows}</table></details>`
}

function axes(spec) {
  const vals = spec.series.flatMap((s) => s.values).filter(isFinite)
  // bars stand on zero; a line shows its own range, so small moves stay visible
  const reach = [spec.y?.min, spec.y?.max].filter(isFinite)  // the agent's own range, never clipping data
  const lo0 = Math.min(...vals, ...reach), hi0 = Math.max(...vals, ...reach)
  const ticks = spec.type === 'line' ? niceTicks(lo0, hi0) : niceTicks(Math.min(0, lo0), Math.max(0, hi0))
  const lo = ticks[0], hi = ticks[ticks.length - 1]
  const y = (v) => T + (H - T - B) * (1 - (v - lo) / (hi - lo))
  const grid = ticks.map((v) => `<line class="dc-grid${v === 0 ? ' zero' : ''}" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/>`
    + `<text class="dc-tick" x="${L - 6}" y="${y(v) + 4}" text-anchor="end">${fmt(v)}</text>`).join('')
  const n = spec.labels.length, band = (W - L - R) / n
  const every = Math.ceil(n / Math.floor((W - L - R) / 56))
  const xl = spec.labels.map((l, j) => (j % every ? '' : `<text class="dc-tick" x="${L + band * (j + 0.5)}" y="${H - B + 18}" text-anchor="middle">${esc(short(l))}</text>`)).join('')
  const cross = `<line class="dc-cross" y1="${T}" y2="${H - B}" x1="0" x2="0"/>`
  return { y, band, grid: grid + xl + cross }
}

function bars(spec) {
  const { y, band, grid } = axes(spec)
  const k = spec.series.length, inner = band * 0.72, w = Math.max(2, inner / k - 2), r = Math.min(4, w / 2)
  const marks = spec.series.map((s, i) => s.values.map((v, j) => {
    if (!isFinite(v)) return ''
    const x = L + band * j + (band - inner) / 2 + i * (inner / k) + 1
    const y0 = y(0), y1 = y(v), up = v >= 0, h = Math.abs(y0 - y1), rr = Math.min(r, h)
    const d = up
      ? `M${x},${y0}V${y1 + rr}Q${x},${y1} ${x + rr},${y1}H${x + w - rr}Q${x + w},${y1} ${x + w},${y1 + rr}V${y0}Z`
      : `M${x},${y0}V${y1 - rr}Q${x},${y1} ${x + rr},${y1}H${x + w - rr}Q${x + w},${y1} ${x + w},${y1 - rr}V${y0}Z`
    return `<path class="dc-bar s${i}" data-j="${j}" d="${d}"/>`
  }).join('')).join('')
  return grid + marks
}

function lines(spec) {
  const { y, band, grid } = axes(spec)
  const x = (j) => L + band * (j + 0.5)
  const dots = spec.labels.length <= 24
  const marks = spec.series.map((s, i) => {
    let d = '', pen = 'M'
    s.values.forEach((v, j) => { if (isFinite(v)) { d += `${pen}${x(j)},${y(v)}`; pen = 'L' } else pen = 'M' })
    const pts = s.values.map((v, j) => (isFinite(v)
      ? `<circle class="dc-dot s${i}${dots ? '' : ' hide'}" data-j="${j}" cx="${x(j)}" cy="${y(v)}" r="4"/>`
      : '')).join('')
    return `<path class="dc-line s${i}" d="${d}"/>${pts}`
  }).join('')
  return grid + marks
}

function pie(spec) {
  const s = spec.series[0], cx = W / 2, cy = (H - 6) / 2 + 3, r = (H - 12) / 2
  const parts = spec.labels.map((l, j) => ({ l, j, v: s.values[j] })).filter((p) => isFinite(p.v) && p.v > 0).slice(0, MAX_SERIES)
  const total = parts.reduce((a, p) => a + p.v, 0)
  if (!total) return ''
  let a0 = -Math.PI / 2
  const pt = (a, rr) => `${cx + rr * Math.cos(a)},${cy + rr * Math.sin(a)}`
  return parts.map((p, i) => {
    const a1 = a0 + (p.v / total) * Math.PI * 2, big = a1 - a0 > Math.PI ? 1 : 0, ri = r * 0.55
    const d = parts.length === 1
      ? `M${pt(a0, r)}A${r},${r} 0 1 1 ${pt(a0 - 0.0001, r)}L${pt(a0 - 0.0001, ri)}A${ri},${ri} 0 1 0 ${pt(a0, ri)}Z`
      : `M${pt(a0, r)}A${r},${r} 0 ${big} 1 ${pt(a1, r)}L${pt(a1, ri)}A${ri},${ri} 0 ${big} 0 ${pt(a0, ri)}Z`
    a0 = a1
    return `<path class="dc-slice s${i}" data-j="${p.j}" d="${d}"/>`
  }).join('')
}

export function chart(text, label = 'Data') {
  const spec = parse(text)
  if (!spec) return null
  const body = spec.type === 'pie' ? pie(spec) : spec.type === 'line' ? lines(spec) : bars(spec)
  if (!body) return null
  const names = spec.type === 'pie' ? spec.labels.slice(0, MAX_SERIES) : spec.series.map((s) => s.name)
  const data = esc(JSON.stringify({ type: spec.type, labels: spec.labels, series: spec.series }))
  return `<figure class="dot-chart" data-chart="${data}">${spec.title ? `<figcaption>${esc(spec.title)}</figcaption>` : ''}`
    + `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="${esc(spec.title || spec.type)}">${body}</svg>`
    + legend(names) + table(spec, label) + '</figure>'
}

// ---- hover: one listener for every chart on the page ----
let active = null
function hide() {
  if (!active) return
  active.querySelector('.dc-tip')?.remove()
  active.classList.remove('hovering')
  active.querySelectorAll('.on').forEach((el) => el.classList.remove('on'))
  active = null
}
function point(e) {
  const fig = e.target.closest?.('.dot-chart'), svg = fig?.querySelector('svg')
  if (!svg || !svg.contains(e.target)) return hide()
  let spec
  try { spec = JSON.parse(fig.dataset.chart) } catch { return hide() }
  const r = svg.getBoundingClientRect(), k = r.width / W, n = spec.labels.length, band = (W - L - R) / n
  let j, x
  if (spec.type === 'pie') {
    j = e.target.dataset?.j
    if (j == null) return hide()
    j = +j; x = e.clientX - r.left
  } else {
    j = Math.floor(((e.clientX - r.left) / k - L) / band)
    if (j < 0 || j >= n) return hide()
    x = (L + band * (j + 0.5)) * k
  }
  if (active !== fig) hide()
  active = fig
  fig.classList.add('hovering')
  fig.querySelectorAll('[data-j]').forEach((el) => el.classList.toggle('on', +el.dataset.j === j))
  const cross = fig.querySelector('.dc-cross')
  if (cross) { cross.setAttribute('x1', x / k); cross.setAttribute('x2', x / k) }
  let rows
  if (spec.type === 'pie') {
    const vals = spec.series[0].values, total = vals.reduce((a, v) => a + (v > 0 ? v : 0), 0)
    rows = `<div><b>${fmt(vals[j])}</b> · ${Math.round((vals[j] / total) * 100)}%</div>`
  } else {
    rows = spec.series.map((s, i) => `<div><i class="dc-sw s${i}"></i>${spec.series.length > 1 ? esc(s.name) + ' ' : ''}<b>${fmt(s.values[j])}</b></div>`).join('')
  }
  let tip = fig.querySelector('.dc-tip')
  if (!tip) { tip = document.createElement('div'); tip.className = 'dc-tip'; fig.appendChild(tip) }
  tip.innerHTML = `<div class="dc-tip-h">${esc(spec.labels[j])}</div>${rows}`
  const fr = fig.getBoundingClientRect(), left = r.left - fr.left + x
  tip.style.left = `${Math.min(Math.max(left, 60), fr.width - 60)}px`
  tip.style.top = `${e.clientY - fr.top}px`
}
if (typeof document !== 'undefined') {
  document.addEventListener('pointermove', point)
  document.addEventListener('pointerdown', point)
}
