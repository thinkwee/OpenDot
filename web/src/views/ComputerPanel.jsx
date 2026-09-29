import { agentFile, openFile } from '../components/FileViewer'
import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Maximize2, Minimize2 } from 'lucide-react'
import { api, getToken, store, useStore } from '../store'
import Mascot, { animalFor, helperAnimal } from '../components/Mascot'
import Boundary from '../components/Boundary'
import '@xterm/xterm/css/xterm.css'
import './Computer.css'

const TABS = [
  ['screen', '🧭'],
  ['terminal', '⌨️'],
  ['files', '🗂️'],
]

function wsURL(path) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws'
  return `${proto}://${location.host}${path}?token=${encodeURIComponent(getToken())}`
}

export default function ComputerPanel({ threadId, onClose, sheet, standalone }) {
  const { t } = useTranslation('computer')
  const { threads, agents, computer, running, workers, steps, watchOwner } = useStore()
  const thread = threads.find((x) => x.id === threadId)
  const members = (thread?.members || []).map((id) => agents.find((a) => a.id === id)).filter(Boolean)
  const [agentId, setAgentId] = useState(members[0]?.id)
  const [tab, setTab] = useState('screen')
  const [max, setMax] = useState(false)
  const [owner, setOwner] = useState(members[0]?.id)
  const c = computer[agentId] || {}
  const agent = agents.find((a) => a.id === agentId)
  const liveAgent = members.find((m) => running[m.id]?.thread_id === threadId)

  // helpers (<agent>-wN) have their own home, terminal and browser tab
  const helpers = helperList(agentId, threadId, running, workers, steps)
  useEffect(() => {
    setOwner(agentId)
  }, [agentId])
  useEffect(() => {
    if (!watchOwner) return
    const root = watchOwner.replace(/-w\d+$/, '')
    if (members.find((m) => m.id === root)) {
      setAgentId(root)
      setTimeout(() => setOwner(watchOwner), 0)
    }
    store.set({ watchOwner: null })
  }, [watchOwner])
  const who = owner && owner !== agentId ? helpers.find((h) => h.id === owner) : null

  // follow whoever is working, and the view they're using
  useEffect(() => {
    if (liveAgent && liveAgent.id !== agentId) setAgentId(liveAgent.id)
  }, [liveAgent?.id])
  useEffect(() => {
    if (c.view && liveAgent) setTab(c.view)
  }, [c.lastEvent])
  useEffect(() => {
    if (!members.find((m) => m.id === agentId)) setAgentId(members[0]?.id)
  }, [threadId])
  useEffect(() => {
    if (!max) return
    const onKey = (e) => {
      if (e.key === 'Escape') setMax(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [max])

  return (
    <>
    {max && <div className="comp-backdrop" onClick={() => setMax(false)} />}
    <aside className={`computer ${sheet ? 'sheet' : ''} ${max ? 'max' : ''} ${standalone ? 'standalone' : ''}`}>
      <header className="comp-head">
        <div className="row gap8">
          <Mascot color={agent?.color} animal={agent && animalFor(agent)} status={agent?.status} size={30} bubble={false} />
          <div>
            <b>{t('title', { name: agent?.name })}</b>
            <small className={`live-pill ${agent?.status !== 'idle' ? 'on' : ''}`}>
              {agent?.status !== 'idle' ? `● ${t('live')}` : t('common:status.idle')}
            </small>
          </div>
        </div>
        <div className="row gap6">
          <button className="icon-btn" onClick={() => setMax(!max)} title={max ? t('shrink') : t('enlarge')}>{max ? <Minimize2 size={17} /> : <Maximize2 size={17} />}</button>
          <button className="icon-btn" onClick={() => (max ? setMax(false) : onClose())} title={t('common:close')}>✕</button>
        </div>
      </header>
      {members.length > 1 && (
        <div className="comp-agents">
          {members.map((m) => (
            <button key={m.id} className={`mini-agent ${m.id === agentId ? 'on' : ''}`} onClick={() => setAgentId(m.id)}>
              <Mascot color={m.color} animal={animalFor(m)} status={m.status} size={22} bubble={false} /> {m.name}
            </button>
          ))}
        </div>
      )}
      {helpers.length > 0 && (
        <div className="comp-agents comp-helpers">
          <button className={`mini-agent ${owner === agentId ? 'on' : ''}`} onClick={() => setOwner(agentId)}>
            <Mascot color={agent?.color} animal={agent && animalFor(agent)} size={22} bubble={false} /> {t('lead')}
          </button>
          {helpers.map((h) => (
            <button key={h.id} className={`mini-agent ${owner === h.id ? 'on' : ''}`} onClick={() => setOwner(h.id)} title={h.title}>
              <Mascot color={agent?.color} animal={helperAnimal(agent, h.i)} status={h.live ? 'working' : 'idle'} size={22} bubble={false} />
              <span className="ellipsis">{h.title || t('helperN', { n: h.i + 1 })}</span>
            </button>
          ))}
        </div>
      )}
      <div className="tabs">
        {TABS.map(([k, icon]) => (
          <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{icon} {t(`tabs.${k}`)}</button>
        ))}
      </div>
      <div className="comp-body">
        <ActivityLine agentId={agentId} owner={owner || agentId} c={c} />
        <Boundary key={tab + (owner || agentId)}>
        {tab === 'screen' && <Screen agentId={agentId} owner={owner || agentId} c={who ? {} : c} onEnlarge={() => setMax(true)} />}
        {tab === 'terminal' && <Terminal agentId={owner || agentId} />}
        {tab === 'files' && <Files agentId={owner || agentId} tick={c.filesTick} />}
        </Boundary>
      </div>
    </aside>
    </>
  )
}

function helperList(agentId, threadId, running, workers, steps) {
  if (!agentId) return []
  const rx = new RegExp(`^${agentId}-w(\\d+)$`)
  const runId = running[agentId]?.thread_id === threadId ? running[agentId].run_id : null
  const tsteps = steps[threadId] || []
  // the helpers of the live run, or else of this agent's latest run that had any
  const lastRun = runId || [...tsteps].reverse().find((s) => rx.test(s.agent_id || ''))?.run_id
  if (!lastRun) return []
  let titles = []
  for (const s of tsteps) {
    if (s.run_id !== lastRun || s.tool !== 'delegate') continue
    let tasks = s.args?.tasks
    if (typeof tasks === 'string') {
      try { tasks = JSON.parse(tasks) } catch { tasks = null }
    }
    if (Array.isArray(tasks)) titles = tasks.map((x) => x?.title || '')
  }
  const out = {}
  for (const s of tsteps) {
    const m = s.run_id === lastRun && (s.agent_id || '').match(rx)
    if (m) out[m[1]] ||= { id: s.agent_id, i: +m[1], title: titles[+m[1]] || '', live: false }
  }
  for (const w of Object.values(workers)) {
    const m = w.parent_id === agentId && (!w.run_id || w.run_id === lastRun) && (w.worker_id || '').match(rx)
    if (!m) continue
    out[m[1]] = { ...(out[m[1]] || { id: w.worker_id, i: +m[1] }), title: w.title || titles[+m[1]] || '', live: !!runId && w.state === 'start' }
  }
  return Object.values(out).sort((a, b) => a.i - b.i)
}

function ActivityLine({ agentId, owner, c }) {
  const { t } = useTranslation('computer')
  const [text, setText] = useState('')
  useEffect(() => {
    setText('')
    const onEvt = (e) => {
      const d = e.detail
      if (d.agent_id === (owner || agentId) && d.view === 'activity') {
        setText(d.text || '')
      }
    }
    window.addEventListener('dot:computer', onEvt)
    return () => window.removeEventListener('dot:computer', onEvt)
  }, [agentId, owner])
  const idle = !text
  return (
    <div className={`comp-activity ${idle ? 'idle' : ''}`}>
      <span className="dot" />
      <span className="ellipsis">{text || t('idleLine')}</span>
    </div>
  )
}

// ---------------- Browser (live CDP screencast + tab strip) ----------------
function Screen({ agentId, owner, c, onEnlarge }) {
  const { t } = useTranslation('computer')
  const [frame, setFrame] = useState(null) // {screenshot, url}
  const [connected, setConnected] = useState(false)
  const activeOwner = owner || agentId
  const wsRef = useRef(null)

  // live stream over the dedicated WebSocket
  useEffect(() => {
    setFrame(null)
    setConnected(false)
    const ws = new WebSocket(wsURL(`/api/computer/${activeOwner}/stream`))
    wsRef.current = ws
    ws.onopen = () => { setConnected(true) }
    ws.onclose = () => { setConnected(false) }
    ws.onerror = () => { setConnected(false) }
    ws.onmessage = (e) => {
      try {
        const d = JSON.parse(e.data)
        if (d.screenshot) setFrame({ screenshot: d.screenshot, url: d.url })
        else if (d.url) setFrame((f) => ({ ...(f || {}), url: d.url }))
      } catch { /* ignore */ }
    }
    return () => { ws.close() }
  }, [activeOwner])

  const fallback = `/api/agents/${activeOwner}/screen?token=${encodeURIComponent(getToken())}&t=${Date.now()}`
  const src = frame?.screenshot || c.screenshot || fallback
  const url = frame?.url || c.url || 'about:blank'

  return (
    <div className="screen">
      <div className="urlbar">
        <span className="dots3"><i /><i /><i /></span>
        <span className="ellipsis">{url}</span>
      </div>
      <div className="screen-view live">
        <span className={`stream-dot ${connected ? '' : 'off'}`}><i />{connected ? t('common:status.live') : t('connecting')}</span>
        {src ? (
          <img src={src} alt={t('browserAlt')} title={t('clickEnlarge')} onClick={onEnlarge}
            onError={(e) => (e.currentTarget.style.display = 'none')} />
        ) : null}
        {!frame && !c.screenshot && (
          <div className="screen-empty">
            <Mascot animal="fox" color="#FFB38A" status="sleeping" size={56} />
            <small className="muted">{t('browserAsleep')}</small>
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------- Terminal (real PTY, xterm.js) ----------------
// pastel ANSI palette matching the app
const TERM_THEME = {
  background: '#1d1826', foreground: '#ece4f5', cursor: '#ffb38a', selectionBackground: '#4a3d5c',
  black: '#2b2233', red: '#ff8a9a', green: '#9be3b4', yellow: '#ffd98a', blue: '#9ec9ff',
  magenta: '#d4b8ff', cyan: '#8fe3e0', white: '#e5dcef',
  brightBlack: '#6f6477', brightRed: '#ffa3b0', brightGreen: '#b5f0c9', brightYellow: '#ffe7ad',
  brightBlue: '#bcdbff', brightMagenta: '#e3cfff', brightCyan: '#b0f0ed', brightWhite: '#ffffff',
}
function Terminal({ agentId }) {
  const { t } = useTranslation('computer')
  const hostRef = useRef(null)
  const termRef = useRef(null)
  const fitRef = useRef(null)
  const wsRef = useRef(null)
  const [takeover, setTakeover] = useState(false)
  const takeoverRef = useRef(false)
  useEffect(() => { takeoverRef.current = takeover }, [takeover])

  useEffect(() => {
    let disposed = false
    let cleanupResize = () => {}
    import('@xterm/xterm').then(({ Terminal: XTerm }) => {
      import('@xterm/addon-fit').then(({ FitAddon }) => {
        if (disposed || !hostRef.current) return
        const term = new XTerm({
          convertEol: true, fontSize: 12.5, lineHeight: 1.25,
          fontFamily: "'JetBrains Mono', ui-monospace, Menlo, monospace",
          theme: TERM_THEME, cursorBlink: false, cursorStyle: 'bar', scrollback: 5000,
        })
        const fit = new FitAddon()
        term.loadAddon(fit)
        term.open(hostRef.current)
        fit.fit()
        termRef.current = term
        fitRef.current = fit

        const ws = new WebSocket(wsURL(`/api/computer/${agentId}/term`))
        wsRef.current = ws
        ws.onmessage = (e) => { term.write(e.data) }
        ws.onopen = () => {
          const dims = fit.proposeDimensions()
          if (dims) ws.send(`\x00resize:${dims.rows},${dims.cols}`)
        }
        term.onData((data) => {
          if (!takeoverRef.current || ws.readyState !== 1) return
          ws.send(data)
          // the shell runs with echo off, so echo the owner's keystrokes locally
          if (data === '\r') term.write('\r\n')
          else if (data === '\x7f') term.write('\b \b')
          else if (data >= ' ') term.write(data)
        })

        const onResize = () => {
          try { fit.fit() } catch { return }
          const dims = fit.proposeDimensions()
          if (dims && ws.readyState === 1) ws.send(`\x00resize:${dims.rows},${dims.cols}`)
        }
        // refit when the panel is enlarged/shrunk, not only on window resize
        const ro = new ResizeObserver(onResize)
        ro.observe(hostRef.current)
        cleanupResize = () => ro.disconnect()
      })
    })
    return () => {
      disposed = true
      cleanupResize()
      wsRef.current?.close()
      termRef.current?.dispose()
    }
  }, [agentId])

  return (
    <div>
      <div className="term-toolbar">
        <small>{t('term.readOnly')}</small>
        <label className="row gap6">
          <input type="checkbox" checked={takeover} onChange={(e) => setTakeover(e.target.checked)} />
          <small>{t('term.typeInto')}</small>
        </label>
      </div>
      <div className="xterm-wrap" ref={hostRef} />
    </div>
  )
}

function Files({ agentId, tick }) {
  const { t } = useTranslation('computer')
  const [path, setPath] = useState('.')
  const [files, setFiles] = useState([])
  useEffect(() => {
    setPath('.')
  }, [agentId])
  useEffect(() => {
    api(`/api/agents/${agentId}/files?path=${encodeURIComponent(path)}`).then((r) => setFiles(r.files)).catch(() => setFiles([]))
  }, [agentId, path, tick])
  const open = (p) => openFile(agentFile(agentId, p))
  return (
    <div className="files">
      <div className="crumbs">
        <button onClick={() => setPath('.')}>🏠 {t('files.home')}</button>
        {path !== '.' && path.split('/').map((seg, i, arr) => (
          <button key={i} onClick={() => setPath(arr.slice(0, i + 1).join('/'))}>/ {seg}</button>
        ))}
      </div>
      {files.length === 0 && <div className="muted pad">{t('files.empty')}</div>}
      {files.map((f) => (
        <button key={f.path} className="file" onClick={() => (f.dir ? setPath(f.path) : open(f.path))}>
          <span>{f.dir ? '📁' : fileIcon(f.path)}</span>
          <span className="ellipsis">{f.path.split('/').pop()}</span>
          <small className="muted">{f.dir ? '' : fmtSize(f.size)}</small>
        </button>
      ))}
    </div>
  )
}


const fileIcon = (p) =>
  /\.(png|jpe?g|gif|svg|webp)$/i.test(p) ? '🖼️' : /\.(py|js|ts|sh)$/i.test(p) ? '📜' : /\.(md|txt)$/i.test(p) ? '📝' : /\.(csv|xlsx|json)$/i.test(p) ? '📊' : /\.html?$/i.test(p) ? '🌐' : '📄'
const fmtSize = (n) => (n == null ? '' : n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`)
