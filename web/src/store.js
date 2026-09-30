// Tiny global store + API client + live WebSocket.
import { useSyncExternalStore } from 'react'

const TOKEN_KEY = 'dot_token'
export const getToken = () => {
  try { return localStorage.getItem(TOKEN_KEY) || '' } catch { return '' }
}
export const setToken = (t) => {
  try { localStorage.setItem(TOKEN_KEY, t) } catch { /* private mode */ }
}

export async function api(path, opts = {}) {
  const r = await fetch(path, {
    ...opts,
    headers: {
      'Content-Type': 'application/json',
      Authorization: `Bearer ${getToken()}`,
      ...(opts.headers || {}),
    },
    body: opts.body && typeof opts.body !== 'string' ? JSON.stringify(opts.body) : opts.body,
    credentials: 'same-origin',
  })
  if (r.status === 401) {
    store.set({ authed: false })
    throw new Error('unauthorized')
  }
  if (!r.ok) throw new Error((await r.text()) || r.statusText)
  const ct = r.headers.get('content-type') || ''
  return ct.includes('json') ? r.json() : r.text()
}

const initial = {
  authed: !!getToken(),
  ready: false,
  connected: false,
  agents: [],
  threads: [],
  messages: {}, // thread_id -> []
  steps: {}, // thread_id -> []
  running: {}, // agent_id -> {thread_id, run_id}
  workers: {}, // worker_id -> {parent_id, title, state, thread_id, result}
  thoughts: {}, // run_id -> text
  streams: {}, // run_id -> accumulated streamed text (cleared once the final message lands)
  approvals: [],
  inboxUnread: 0,
  computer: {}, // agent_id -> {screenshot, url, terminal: [], files: n}
  pages: [],
  model: '',
  hookToken: '',
  toast: null,
}

let state = initial
const subs = new Set()
export const store = {
  get: () => state,
  set: (patch) => {
    state = { ...state, ...(typeof patch === 'function' ? patch(state) : patch) }
    subs.forEach((f) => f())
  },
  subscribe: (f) => {
    subs.add(f)
    return () => subs.delete(f)
  },
}
export const useStore = (sel = (s) => s) => useSyncExternalStore(store.subscribe, () => sel(store.get()))

export function toast(text) {
  store.set({ toast: { text, id: Date.now() } })
  setTimeout(() => store.set((s) => (s.toast?.text === text ? { toast: null } : {})), 3200)
}

// runs still in flight on the server, so a refreshed page shows them working
function liveState(live = []) {
  const running = {}
  const streams = {}
  const thoughts = {}
  const workers = {}
  for (const r of live) {
    running[r.agent_id] = { thread_id: r.thread_id, run_id: r.run_id }
    if (r.text) streams[r.run_id] = r.text
    if (r.thought) thoughts[r.run_id] = r.thought
    for (const w of r.workers || []) workers[w.worker_id + ':' + w.title] = w
  }
  return { running, streams, thoughts, workers }
}

export async function bootstrap() {
  const b = await api('/api/bootstrap')
  store.set({
    ready: true,
    authed: true,
    agents: b.agents,
    threads: b.threads,
    approvals: b.approvals,
    inboxUnread: b.inbox_unread,
    model: b.model,
    hookToken: b.hook_token,
    ...liveState(b.live),
  })
  connectWS()
}

// After the socket dropped or fell behind, events may have been missed: reload
// what's on screen from REST instead of trusting the local copy.
let syncing = null
async function resync() {
  if (syncing) return syncing
  syncing = (async () => {
    try {
      const b = await api('/api/bootstrap')
      store.set({
        agents: b.agents,
        threads: b.threads,
        approvals: b.approvals,
        inboxUnread: b.inbox_unread,
        ...liveState(b.live),
      })
      await Promise.all(Object.keys(store.get().messages).map((tid) => loadThread(tid).catch(() => {})))
    } catch { /* offline; the next reconnect tries again */ }
  })()
  try { await syncing } finally { syncing = null }
}

export async function loadThread(tid) {
  const r = await api(`/api/threads/${tid}/messages`)
  store.set((s) => ({
    messages: { ...s.messages, [tid]: r.messages },
    steps: { ...s.steps, [tid]: r.steps },
  }))
}

let ws = null
let wsTimer = null
let wsEver = false
let lastPong = 0
function connectWS() {
  if (ws && ws.readyState <= 1) return
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  const sock = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(getToken())}`)
  ws = sock
  sock.onopen = () => {
    store.set({ connected: true })
    if (wsEver) resync()
    wsEver = true
    lastPong = Date.now()
    clearInterval(wsTimer)
    wsTimer = setInterval(() => {
      if (sock.readyState !== 1) return
      // a phone that slept or switched networks can leave a socket that looks
      // open but is dead; no pong for a while → drop it and reconnect
      if (Date.now() - lastPong > 70000) return sock.close()
      sock.send('ping')
    }, 25000)
  }
  sock.onclose = () => {
    if (ws !== sock) return
    store.set({ connected: false })
    setTimeout(connectWS, 2000)
  }
  sock.onmessage = (e) => {
    lastPong = Date.now()
    handle(JSON.parse(e.data))
  }
}

// Mobile browsers freeze background tabs and quietly kill their sockets.
let hiddenAt = 0
document.addEventListener('visibilitychange', () => {
  if (document.hidden) {
    hiddenAt = Date.now()
    return
  }
  if (!store.get().authed) return
  if (!ws || ws.readyState > 1) connectWS()
  else if (hiddenAt && Date.now() - hiddenAt > 15000) resync()
})

// one line of plain text from Markdown, for list previews (mirrors server._plain)
function plain(md) {
  return md
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/[*_`#>~]+/g, '')
    .replace(/\s+/g, ' ')
    .trim()
}

function upsert(list, item, key = 'id') {
  const i = list.findIndex((x) => x[key] === item[key])
  if (i === -1) return [...list, item]
  const copy = list.slice()
  copy[i] = { ...copy[i], ...item }
  return copy
}

function handle(ev) {
  // extension views: window.addEventListener('dot:<kind>', (e) => e.detail)
  try { window.dispatchEvent(new CustomEvent(`dot:${ev.kind}`, { detail: ev })) } catch { /* ignore */ }
  const s = store.get()
  switch (ev.kind) {
    case 'message': {
      const m = ev.message
      const list = s.messages[m.thread_id]
      const threads = s.threads
        .map((t) => (t.id === m.thread_id ? {
          ...t, updated: m.created,
          last: { role: m.role, agent_id: m.agent_id, text: plain(m.content || '').slice(0, 140), at: m.created },
          needs_you: m.role === 'agent' && !!m.meta?.choices?.length,
        } : t))
        .sort((a, b) => b.updated - a.updated)
      const streams = { ...s.streams }
      if (m.meta?.run_id) delete streams[m.meta.run_id]
      store.set({
        messages: list ? { ...s.messages, [m.thread_id]: upsert(list, m) } : s.messages,
        threads,
        streams,
      })
      break
    }
    case 'delta':
      store.set({ streams: { ...s.streams, [ev.run_id]: ev.reset ? '' : (s.streams[ev.run_id] || '') + ev.text } })
      break
    case 'resync':
      resync()
      break
    case 'step': {
      const st = ev.step
      const list = s.steps[st.thread_id]
      if (list) store.set({ steps: { ...s.steps, [st.thread_id]: upsert(list, st) } })
      break
    }
    case 'thought': {
      // the streamed text was narration before a tool call, not the answer
      const streams = { ...s.streams }
      delete streams[ev.run_id]
      store.set({ thoughts: { ...s.thoughts, [ev.run_id]: ev.text }, streams })
      break
    }
    case 'run': {
      const running = { ...s.running }
      if (ev.state === 'start') running[ev.agent_id] = { thread_id: ev.thread_id, run_id: ev.run_id }
      else delete running[ev.agent_id]
      const streams = { ...s.streams }
      if (ev.state === 'end') delete streams[ev.run_id]
      store.set({ running, streams })
      break
    }
    case 'agent':
      store.set({
        agents: s.agents.map((a) =>
          a.id === ev.agent_id ? { ...a, status: ev.status, status_text: ev.status_text } : a,
        ),
      })
      break
    case 'agent_created':
      store.set({ agents: upsert(s.agents, ev.agent), threads: upsert(s.threads, ev.thread).sort((a, b) => b.updated - a.updated) })
      break
    case 'agent_updated':
      store.set({ agents: upsert(s.agents, ev.agent) })
      break
    case 'agent_deleted':
      store.set({
        agents: s.agents.filter((a) => a.id !== ev.agent_id),
        threads: s.threads
          .map((t) => ({ ...t, members: t.members.filter((m) => m !== ev.agent_id) }))
          .filter((t) => t.members.length && !(t.kind === 'dm' && !t.members.length)),
      })
      break
    case 'thread':
      store.set({ threads: upsert(s.threads, ev.thread) })
      break
    case 'thread_gone':
      store.set({ threads: s.threads.filter((t) => t.id !== ev.thread_id) })
      if (location.hash.includes(ev.thread_id)) location.hash = '#/chats'
      break
    case 'worker':
      store.set({ workers: { ...s.workers, [ev.worker_id + ':' + ev.title]: ev } })
      break
    case 'approval': {
      const ap = ev.approval
      const approvals = ap.status === 'pending' ? upsert(s.approvals, ap) : s.approvals.filter((a) => a.id !== ap.id)
      store.set({ approvals })
      break
    }
    case 'inbox':
      store.set({ inboxUnread: s.inboxUnread + 1 })
      break
    case 'computer': {
      const base = ev.agent_id.split('-w')[0]
      const c = { terminal: [], ...(s.computer[base] || {}) }
      if (ev.view === 'browser') {
        if (ev.screenshot) c.screenshot = ev.screenshot
        if (ev.url) c.url = ev.url
        c.view = 'screen'
      } else if (ev.view === 'terminal') {
        const term = c.terminal.slice(-60)
        if (ev.input !== undefined) term.push({ type: 'in', text: ev.input, who: ev.agent_id })
        if (ev.output !== undefined) term.push({ type: 'out', text: ev.output, code: ev.exit_code })
        c.terminal = term
        c.view = 'terminal'
      } else if (ev.view === 'files') {
        c.filesTick = (c.filesTick || 0) + 1
        c.lastFile = ev.path
      }
      c.lastEvent = Date.now()
      store.set({ computer: { ...s.computer, [base]: c } })
      break
    }
    case 'page':
      store.set({ pages: [{ url: ev.url, title: ev.title, agent_id: ev.agent_id }, ...s.pages] })
      break
    default:
  }
}
