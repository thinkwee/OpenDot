import { useEffect, useState } from 'react'
import { useTranslation, Trans } from 'react-i18next'
import { api, toast, useStore } from '../store'
import Mascot from '../components/Mascot'
import './Devices.css'

const OS_ICON = (os = '') =>
  /darwin|mac/i.test(os) ? '🍎' : /windows/i.test(os) ? '🪟' : /linux/i.test(os) ? '🐧' : '💻'

// labels live in devices.json → caps.<name>
const CAP_ICON = {
  shell: '⌨️', applescript: '📜', open: '🚀', screenshot: '📸',
  clipboard: '📋', notify: '🔔', list_apps: '🗂️', files: '📁',
  mouse: '🖱️', keyboard: '⌨️',
}

function timeAgo(ts, t) {
  if (!ts) return t('ago.never')
  const s = Date.now() / 1000 - ts
  if (s < 60) return t('common:time.now')
  if (s < 3600) return t('ago.min', { n: Math.floor(s / 60) })
  if (s < 86400) return t('ago.hour', { n: Math.floor(s / 3600) })
  return t('ago.day', { n: Math.floor(s / 86400) })
}

export default function Devices() {
  const { t } = useTranslation('devices')
  const [devices, setDevices] = useState(null)
  const [adding, setAdding] = useState(false)

  const load = () => api('/api/nodes').then(setDevices).catch(() => setDevices([]))
  useEffect(() => {
    load()
    const timer = setInterval(load, 8000)
    const onEv = () => load()
    window.addEventListener('dot:device', onEv)
    return () => { clearInterval(timer); window.removeEventListener('dot:device', onEv) }
  }, [])

  const rename = async (d) => {
    const name = prompt(t('renamePrompt'), d.name)
    if (!name || name === d.name) return
    await api(`/api/nodes/${d.id}`, { method: 'PATCH', body: { name } })
    load()
  }
  const toggle = async (d) => {
    await api(`/api/nodes/${d.id}`, { method: 'PATCH', body: { approved: !d.approved } })
    load()
  }
  const remove = async (d) => {
    if (!confirm(t('removeConfirm', { name: d.name }))) return
    await api(`/api/nodes/${d.id}`, { method: 'DELETE' })
    toast(t('removed', { name: d.name }))
    load()
  }

  return (
    <div>
      <header className="page-head">
        <div>
          <h1>💻 {t('title')}</h1>
          <p className="muted">{t('lead')}</p>
        </div>
        <button className="btn" onClick={() => setAdding(true)}>＋ {t('add')}</button>
      </header>

      {devices === null && <div className="muted">{t('common:loading')}</div>}

      {devices && devices.length === 0 && (
        <div className="empty-card">
          <Mascot animal="fox" color="#FFB38A" status="idle" size={64} emoji="💻" />
          <p>{t('empty.title')}</p>
          <p className="muted small">{t('empty.hint')}</p>
          <button className="btn" onClick={() => setAdding(true)}>＋ {t('empty.addFirst')}</button>
        </div>
      )}

      {devices && devices.length > 0 && (
        <div className="device-grid">
          {devices.map((d) => (
            <div key={d.id} className={`card device-card ${d.online ? 'on' : 'off'} ${d.approved ? '' : 'paused'}`}>
              <div className="row between">
                <div className="row gap8">
                  <span className="device-icon">{OS_ICON(d.os)}</span>
                  <div>
                    <b>{d.name}</b>
                    <div className="row gap6">
                      <span className={`dot ${d.online ? 'ok' : 'bad'}`} />
                      <small className="muted">{d.online ? t('online') : t('lastSeen', { when: timeAgo(d.last_seen, t) })}</small>
                    </div>
                  </div>
                </div>
              </div>
              <div className="row wrap gap6 mt8">
                {(d.capabilities || []).map((c) => (
                  <span key={c} className="chip small">{CAP_ICON[c] ? `${CAP_ICON[c]} ${t(`caps.${c}`)}` : c}</span>
                ))}
                {(d.capabilities || []).length === 0 && <span className="muted small">{t('noCaps')}</span>}
              </div>
              <div className="row between mt8">
                <label className="row gap6">
                  <span className="switch">
                    <input type="checkbox" checked={!!d.approved} onChange={() => toggle(d)} />
                    <span />
                  </span>
                  <small className="muted">{d.approved ? t('canUse') : t('paused')}</small>
                </label>
                <div className="row gap6">
                  <button className="btn ghost sm" onClick={() => rename(d)}>{t('rename')}</button>
                  <button className="btn ghost sm danger" onClick={() => remove(d)}>{t('remove')}</button>
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      {adding && <AddDeviceModal onClose={() => { setAdding(false); load() }} />}
    </div>
  )
}

function AddDeviceModal({ onClose }) {
  const { t } = useTranslation('devices')
  const { agents } = useStore()
  const [pair, setPair] = useState(null)
  const [err, setErr] = useState('')

  useEffect(() => {
    api('/api/pair/new', { method: 'POST', body: { origin: location.origin } })
      .then(setPair)
      .catch((e) => setErr(e.message))
  }, [])

  const origin = location.origin
  const dlCmd = `curl -fsSL ${origin}/dl/dot_node.py -o dot_node.py`
  const pairCmd = pair ? `python3 dot_node.py pair ${origin} ${pair.code}` : ''
  const macCmd = pair
    ? `curl -fsSL ${origin}/dl/dot_node.py -o /tmp/dot_node.py && python3 /tmp/dot_node.py pair ${origin} ${pair.code}`
    : ''

  const copy = (text) => {
    navigator.clipboard?.writeText(text).catch(() => {})
    toast(t('common:copied'))
  }

  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal device-modal" onClick={(e) => e.stopPropagation()}>
        <h2>{t('add')}</h2>
        <p className="muted small">{t('modal.intro')}</p>

        {err && <p className="err">{err}</p>}
        {!pair && !err && <p className="muted">{t('modal.gettingCode')}</p>}

        {pair && (
          <>
            <div className="code-box">
              <code>{dlCmd}</code>
              <button className="icon-btn" onClick={() => copy(dlCmd)}>📋</button>
            </div>
            <div className="code-box">
              <code>{pairCmd}</code>
              <button className="icon-btn" onClick={() => copy(pairCmd)}>📋</button>
            </div>
            <p className="muted small">{t('modal.oneLine')}</p>
            <div className="code-box">
              <code className="ellipsis">{macCmd}</code>
              <button className="icon-btn" onClick={() => copy(macCmd)}>📋</button>
            </div>
            <p className="muted small"><Trans t={t} i18nKey="modal.keepRunning" components={{ code: <code /> }} /></p>
            <p className="muted small">{t('modal.expires')}</p>
          </>
        )}

        <div className="row between mt8">
          <span className="muted small">{agents.length ? t('modal.agentsCan', { count: agents.length }) : ''}</span>
          <button className="btn" onClick={onClose}>{t('modal.done')}</button>
        </div>
      </div>
    </div>
  )
}
