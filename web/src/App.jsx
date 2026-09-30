import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, bootstrap, useStore, store } from './store'
import { syncLang } from './i18n'
import Mascot, { animalFor } from './components/Mascot'
import Deco from './components/Deco'
import { FileViewerHost } from './components/FileViewer'
import { CalendarDays, ListChecks, MessageCircle, Inbox as InboxIcon, Settings as SettingsIcon } from 'lucide-react'
import ChatList from './views/ChatList'
import ChatView from './views/ChatView'
import ComputerPanel from './views/ComputerPanel'
import Team from './views/Team'
import Inbox from './views/Inbox'
import Calendar from './views/Calendar'
import Todo from './views/Todo'
import Automations from './views/Automations'
import Memory from './views/Memory'
import Settings from './views/Settings'
import Pair from './views/Pair'
import About, { StarLink } from './views/About'
import Examples from './views/Examples'
import Boundary from './components/Boundary'

// your agents (like a contact list) and what needs you, plus settings. An agent's card
// opens from its chat; everything else you just ask for in chat.
const NAV = [
  ['chats', <MessageCircle size={21} strokeWidth={2.2} />, 'nav.agents'],
  ['todo', <ListChecks size={21} strokeWidth={2.2} />, 'nav.todo'],
  ['calendar', <CalendarDays size={21} strokeWidth={2.2} />, 'nav.calendar'],
  ['inbox', <InboxIcon size={21} strokeWidth={2.2} />, 'nav.inbox'],
]
const SETTINGS_ICON = <SettingsIcon size={21} strokeWidth={2.2} />
// old links keep working
const ALIASES = { office: 'chats', library: 'chats', connectors: 'settings' }

function parseHash() {
  const h = location.hash.replace(/^#\/?/, '')
  const [path, qs] = h.split('?')
  const [raw, id, sub] = path.split('/')
  const view = ALIASES[raw] || raw || 'chats'
  return { view, id, sub, q: new URLSearchParams(qs || '') }
}

export function go(view, id) {
  location.hash = `#/${view}${id ? '/' + id : ''}`
}

function useRoute() {
  const [r, setR] = useState(parseHash())
  useEffect(() => {
    const f = () => setR(parseHash())
    window.addEventListener('hashchange', f)
    return () => window.removeEventListener('hashchange', f)
  }, [])
  return r
}

function useWide(px) {
  const [w, setW] = useState(window.innerWidth >= px)
  useEffect(() => {
    const f = () => setW(window.innerWidth >= px)
    window.addEventListener('resize', f)
    return () => window.removeEventListener('resize', f)
  }, [px])
  return w
}

export default function App() {
  const route = useRoute()
  const { authed, ready, inboxUnread, approvals, connected, threads, toast, watchOwner } = useStore()
  const [showComputer, setShowComputer] = useState(false)
  useEffect(() => {
    if (watchOwner) setShowComputer(true)
  }, [watchOwner])
  const desktop = useWide(900)
  const { t } = useTranslation(['common', 'app'])

  useEffect(() => {
    if (authed && !ready) bootstrap().then(() => syncLang(api)).catch(() => store.set({ authed: false }))
  }, [authed, ready])
  // first visit after pairing: show the guide once
  useEffect(() => {
    if (!ready) return
    try {
      if (!localStorage.getItem('dot_intro_seen')) {
        localStorage.setItem('dot_intro_seen', '1')
        if (!location.hash.includes('embed=1') && (!location.hash || location.hash === '#/' || location.hash.startsWith('#/chats'))) go('about')
      }
    } catch { /* ignore */ }
  }, [ready])

  if (route.view === 'pair' || !authed) return <Pair code={route.q.get('code')} />
  if (!ready)
    return (
      <div className="splash">
        <Deco />
        <Mascot animal="fox" color="#FFB38A" status="thinking" size={88} />
        <p>{t('app:waking')}</p>
      </div>
    )

  const view = route.view
  // ?embed=1 — for showing a view inside another app's web view: no rail / tab bar, no first-run redirect
  const embed = route.q.get('embed') === '1'
  if (view === 'computer' && route.id)
    return (
      <div className={`app mobile ${embed ? 'embed' : ''}`}>
        <ComputerPanel threadId={route.id} onClose={() => history.back()} sheet={false} standalone />
        <FileViewerHost />
      </div>
    )
  const threadId = view === 'chats' ? route.id || (desktop ? threads[0]?.id : null) : null
  const badge = inboxUnread + approvals.length

  const main = {
    team: <Team agentId={route.id} sub={route.sub} />,
    inbox: <Inbox />,
    calendar: <Calendar />,
    todo: <Todo />,
    automations: <Automations />,
    memory: <Memory agentId={route.id} />,
    settings: <Settings sub={route.id} />,
    about: <About />,
    examples: <Examples />,
  }[view]

  return (
    <div className={`app ${desktop ? 'desktop' : 'mobile'} ${showComputer && threadId ? 'with-computer' : ''} ${embed ? 'embed' : ''}`}>
      {desktop && !embed && (
        <nav className="rail">
          <Deco preset="rail" seed={2} />
          <div className="rail-logo" title={t('nav.whatIs')} role="button" onClick={() => go('about')}>
            <img src="/logo.svg" width={40} height={40} alt="OpenDot" style={connected ? null : { filter: 'grayscale(1)', opacity: 0.5 }} />
          </div>
          {NAV.map(([k, icon, label]) => (
            <button key={k} className={`rail-btn ${view === k ? 'on' : ''}`} onClick={() => go(k)} title={t(label)}>
              <span className="rail-ico">{icon}</span>
              <span className="rail-lbl">{t(label)}</span>
              {k === 'inbox' && badge > 0 && <span className="dot-badge">{badge}</span>}
            </button>
          ))}
          <StarLink className="rail-btn rail-gh" label={t('nav.star')} />
          <button className={`rail-btn rail-settings ${view === 'settings' ? 'on' : ''}`} onClick={() => go('settings')} title={t('nav.settings')}>
            <span className="rail-ico">{SETTINGS_ICON}</span>
            <span className="rail-lbl">{t('nav.settings')}</span>
          </button>
          <div className={`conn ${connected ? 'on' : ''}`} title={connected ? t('status.live') : t('status.reconnecting')} />
        </nav>
      )}

      {view === 'chats' ? (
        <>
          {(desktop || !threadId) && <ChatList active={threadId} />}
          {threadId && (
            <ChatView
              key={threadId}
              threadId={threadId}
              desktop={desktop}
              computerOpen={showComputer}
              onToggleComputer={() => setShowComputer((v) => !v)}
            />
          )}
          {threadId && showComputer && (
            <ComputerPanel threadId={threadId} onClose={() => setShowComputer(false)} sheet={!desktop} />
          )}
          {threadId && !showComputer && <LivePip threadId={threadId} onOpen={() => setShowComputer(true)} />}
          {desktop && !threadId && <div className="empty-main"><Deco seed={1} /><Mascot animal="fox" color="#FFB38A" size={96} /><p>{t('app:pickSomeone')}</p><button className="btn ghost sm" onClick={() => go('examples')}>💡 {t('nav.examples')}</button></div>}
        </>
      ) : (
        <main className="page"><Boundary key={view}>{main}</Boundary></main>
      )}

      <FileViewerHost />

      {!desktop && !embed && !(view === 'chats' && threadId) && (
        <nav className="tabbar">
          {[...NAV, ['settings', SETTINGS_ICON, 'nav.settings']].map(([k, icon, label]) => (
            <button key={k} className={view === k ? 'on' : ''} onClick={() => go(k)}>
              <span>{icon}</span>
              <small>{t(label)}</small>
              {k === 'inbox' && badge > 0 && <span className="dot-badge">{badge}</span>}
            </button>
          ))}
        </nav>
      )}
      {toast && <div className="toast" key={toast.id}>{toast.text}</div>}
    </div>
  )
}

// while an agent in this chat is working, a small "watch live" card floats in the corner
function LivePip({ threadId, onOpen }) {
  const { threads, agents, running } = useStore()
  const { t } = useTranslation('app')
  const thread = threads.find((t) => t.id === threadId)
  const busy = (thread?.members || [])
    .map((id) => agents.find((a) => a.id === id))
    .filter((a) => a && running[a.id]?.thread_id === threadId)
  if (!busy.length) return null
  const a = busy[0]
  return (
    <button className="live-pip" onClick={onOpen} style={{ '--c': a.color }}>
      <Mascot color={a.color} animal={animalFor(a)} status={a.status} size={34} bubble={false} />
      <span className="grow">
        <b>{t('isWorking', { name: a.name })}</b>
        <small className="ellipsis">{a.status_text || t('common:status.thinking')}</small>
      </span>
      <span className="live-pip-cta">🖥️ {t('watch')}</span>
    </button>
  )
}
