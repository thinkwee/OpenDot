import { marked } from 'marked'
import DOMPurify from 'dompurify'
import i18n from './i18n'
import { chart } from './chart'

const t = (k, o) => i18n.t(k, { ns: 'common', ...o })
const loc = () => (i18n.language === 'zh' ? 'zh-CN' : [])

marked.setOptions({ breaks: true, gfm: true })
// **bold** that ends on punctuation right before a letter (“**「重点」**之后”) isn't bold
// by the CommonMark rules, which assume a space after it, as in English; Chinese has none
marked.use({
  extensions: [{
    name: 'cjkStrong',
    level: 'inline',
    start: (src) => src.indexOf('**'),
    tokenizer(src) {
      const m = /^\*\*(?!\s)([^\n]*?\S)\*\*/.exec(src)
      if (m) return { type: 'cjkStrong', raw: m[0], tokens: this.lexer.inlineTokens(m[1]) }
    },
    renderer(token) { return `<strong>${this.parser.parseInline(token.tokens)}</strong>` },
  }],
  // ```chart blocks draw as a chart; until the block is complete (still streaming) it stays code
  renderer: { code({ text, lang }) { return (lang === 'chart' && chart(text, t('chartData'))) || false } },
})

export function md(text) {
  // make agent-published page paths clickable (they need the token when no cookie is set)
  const tok = (() => { try { return localStorage.getItem('dot_token') || '' } catch { return '' } })()
  const linked = (text || '').replace(/(^|[\s(*`])(\/pages\/[\w-]+\/[\w-]+)/g, (_, pre, p) => `${pre}[${p.split('/').pop()} ↗](${p}?token=${encodeURIComponent(tok)})`)
  const html = DOMPurify.sanitize(marked.parse(linked))
  // open links in a new tab
  return html.replace(/<a /g, '<a target="_blank" rel="noopener" ')
}

export function ago(ts) {
  if (!ts) return ''
  const d = Date.now() / 1000 - ts
  if (d < 60) return t('time.now')
  if (d < 3600) return t('time.min', { n: Math.floor(d / 60) })
  if (d < 86400) return t('time.hour', { n: Math.floor(d / 3600) })
  return new Date(ts * 1000).toLocaleDateString(loc())
}

export function when(ts) {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  return d.toLocaleString(loc(), { weekday: 'short', hour: '2-digit', minute: '2-digit', month: 'short', day: 'numeric' })
}

// when a timed routine fires: a one-off at its set time, a repeating one at its next run
export function routineWhen(r) {
  return when(r.kind === 'once' ? r.next_run || new Date(r.schedule).getTime() / 1000 : r.next_run)
}

// "0 10 * * 3,6" → "Every Wed, Sat 10:00"; anything unusual stays as written
export function cronText(expr) {
  const p = (expr || '').trim().split(/\s+/)
  if (p.length !== 5 || !/^\d+$/.test(p[0]) || !/^\d+$/.test(p[1])) return expr
  const [mi, h, dom, mon, dow] = p
  const time = `${h.padStart(2, '0')}:${mi.padStart(2, '0')}`
  const wd = (n) => new Date(2024, 0, 7 + (Number(n) % 7)).toLocaleDateString(loc(), { weekday: 'short' }) // 2024-01-07 is a Sunday
  if (dom === '*' && mon === '*' && dow === '*') return t('cron.daily', { time })
  if (dom === '*' && mon === '*' && dow === '1-5') return t('cron.weekdays', { time })
  if (dom === '*' && mon === '*' && /^[\d,]+$/.test(dow)) return t('cron.weekly', { days: dow.split(',').map(wd).join(t('cron.sep')), time })
  if (/^\d+$/.test(dom) && mon === '*' && dow === '*') return t('cron.monthly', { d: dom, time })
  if (/^\d+$/.test(dom) && /^\d+$/.test(mon) && dow === '*') {
    const date = new Date(2024, Number(mon) - 1, Number(dom)).toLocaleDateString(loc(), { month: 'short', day: 'numeric' })
    return t('cron.yearly', { date, time })
  }
  return expr
}

// a message's time: "14:05" today, "Mon 14:05" this week, "Oct 3 14:05" before that
export function clock(ts) {
  if (!ts) return ''
  const d = new Date(ts * 1000), now = new Date()
  const hm = d.toLocaleTimeString(loc(), { hour: '2-digit', minute: '2-digit' })
  if (d.toDateString() === now.toDateString()) return hm
  const days = (now - d) / 86400000
  if (days < 6) return `${d.toLocaleDateString(loc(), { weekday: 'short' })} ${hm}`
  const opts = { month: 'short', day: 'numeric', ...(d.getFullYear() !== now.getFullYear() && { year: 'numeric' }) }
  return `${d.toLocaleDateString(loc(), opts)} ${hm}`
}

export function fullTime(ts) {
  return ts ? new Date(ts * 1000).toLocaleString(loc()) : ''
}

// how long something took, in seconds → "0.4s", "12s", "3m 05s", "1h 02m"
export function took(sec) {
  if (sec == null || sec < 0) return ''
  if (sec < 10) return `${sec.toFixed(1)}s`
  if (sec < 60) return `${Math.round(sec)}s`
  const s = Math.round(sec), m = Math.floor(s / 60), h = Math.floor(m / 60)
  if (h) return `${h}h ${String(m % 60).padStart(2, '0')}m`
  return `${m}m ${String(s % 60).padStart(2, '0')}s`
}

// icon per tool; the verb comes from common.json → tools.<name>
export const TOOL_META = {
  shell: '⌨️', python: '🐍', read_file: '📄', write_file: '✏️', list_files: '🗂️',
  web_search: '🔎', web_fetch: '🌐', browser: '🧭', remember: '🧠', forget: '🫧',
  notify: '🔔', publish_page: '✨', schedule: '⏰', list_automations: '⏰',
  cancel_automation: '⏰', handoff: '🤝', delegate: '🐣', send_email: '✉️',
  watch: '👀', watch_done: '👀', offer_choices: '👉', suggest_routine: '⏰', create_agent: '🐣',
}

export function stepLabel(st) {
  const a = st.args || {}
  const icon = TOOL_META[st.tool] || '🔌'
  const verb = TOOL_META[st.tool] ? t(`tools.${st.tool}`) : st.tool.replace(/^mcp__/, '').replace(/__/g, ' · ')
  let obj = ''
  if (st.tool === 'shell') obj = a.command
  else if (st.tool === 'python') obj = (a.code || '').split('\n')[0]
  else if (st.tool === 'web_search') obj = `“${a.query}”`
  else if (st.tool === 'web_fetch') obj = a.url
  else if (st.tool === 'browser') obj = `${a.action} ${a.url || a.text || a.selector || ''}`
  else if (['read_file', 'write_file', 'list_files'].includes(st.tool)) obj = a.path || ''
  else if (st.tool === 'remember') obj = a.fact
  else if (st.tool === 'handoff') obj = `${a.agent}`
  else if (st.tool === 'delegate') obj = t('tools.inParallel', { n: (a.tasks || []).length })
  else if (st.tool === 'schedule') obj = `${a.name} (${a.schedule || a.event_filter || ''})`
  else if (st.tool === 'publish_page') obj = a.title || a.slug
  else if (st.tool === 'notify') obj = a.title
  else if (st.tool === 'watch') obj = a.name
  else if (st.tool === 'create_agent') obj = a.name || a.responsibility
  else if (st.tool === 'offer_choices') obj = (a.options || []).join(' · ')
  return { icon, verb, obj: (obj || '').toString().slice(0, 90) }
}

export const COLORS = ['#FFB38A', '#8FD6B8', '#B9A6F2', '#8EC5FF', '#FFD37A', '#FF9EC4', '#9EE3E0', '#C8E68C']
export const EMOJIS = ['✨', '🔎', '🛠️', '🪶', '📚', '🎨', '💼', '🍳', '🏃', '💰', '🎵', '🌱', '✈️', '🧪']
