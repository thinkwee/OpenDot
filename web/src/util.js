import { marked } from 'marked'
import DOMPurify from 'dompurify'
import i18n from './i18n'

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
