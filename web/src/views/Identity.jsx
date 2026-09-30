import { useEffect, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { api, toast, useStore } from '../store'
import Mascot, { animalFor } from '../components/Mascot'
import { errOf, publicUrlText } from './idWizard'
import './Identity.css'

const WORKER_SNIPPET = `export default {
  async email(message, env, ctx) {
    const text = await new Response(message.raw).text()
    await fetch("HOOK_URL/AGENT_NAME", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        from: message.from, to: message.to,
        subject: message.headers.get("subject") || "",
        text,
      }),
    })
  },
}`

export default function Identity() {
  const { t } = useTranslation('identity')
  const { agents, hookToken } = useStore()
  const [selected, setSelected] = useState('')
  useEffect(() => {
    if (!selected && agents.length) setSelected(agents[0].id)
  }, [agents, selected])
  const agent = agents.find((a) => a.id === selected)

  return (
    <div>
      <header className="page-head">
        <div>
          <h1>{t('page.title')}</h1>
          <p className="muted">{t('page.intro')}</p>
        </div>
      </header>

      {agents.length === 0 && (
        <div className="empty-card">
          <Mascot animal="fox" color="#FFB38A" size={56} />
          <p className="muted small">{t('page.empty')}</p>
        </div>
      )}

      <div className="id-cards">
        {agents.map((a) => (
          <IdCard key={a.id} agent={a} on={a.id === selected} onClick={() => setSelected(a.id)} />
        ))}
      </div>

      {agent && <AgentIdentity key={agent.id} agent={agent} hookToken={hookToken} />}
    </div>
  )
}

function IdCard({ agent, on, onClick }) {
  const { t } = useTranslation('identity')
  const [has, setHas] = useState(null)
  useEffect(() => {
    Promise.all([
      api(`/api/identity/email/${agent.id}`).catch(() => ({})),
      api(`/api/identity/phone/${agent.id}`).catch(() => ({})),
    ]).then(([email, phone]) =>
      setHas({
        email: !!email.address,
        phone: !!phone.from_number,
      }),
    )
  }, [agent.id])
  return (
    <button className={`card id-card ${on ? 'on' : ''}`} onClick={onClick}>
      <Mascot color={agent.color} animal={animalFor(agent)} emoji={agent.emoji} status={agent.status} size={52} />
      <div className="id-card-info">
        <b>{agent.name}</b>
        <span className="muted small">{agent.role}</span>
        <div className="row gap6 wrap mt6">
          {has?.email && <span className="pill">📧</span>}
          {has?.phone && <span className="pill">📱</span>}
          {has && !has.email && !has.phone && (
            <span className="muted small">{t('page.notSetUp')}</span>
          )}
        </div>
      </div>
    </button>
  )
}

const TABS = ['email', 'phone']

export function AgentIdentity({ agent, hookToken }) {
  const { t } = useTranslation('identity')
  const [tab, setTab] = useState('email')
  return (
    <section className="section card id-detail">
      <div className="row gap8 id-detail-head">
        <Mascot color={agent.color} animal={animalFor(agent)} emoji={agent.emoji} status={agent.status} size={44} />
        <div>
          <h3 style={{ margin: 0 }}>{agent.name}</h3>
          <span className="muted small">{agent.tagline || agent.role}</span>
        </div>
      </div>
      <div className="seg id-tabs">
        {TABS.map((k) => (
          <button key={k} className={tab === k ? 'on' : ''} onClick={() => setTab(k)}>{t(`tabs.${k}`)}</button>
        ))}
      </div>
      {tab === 'email' && <EmailPanel agent={agent} hookToken={hookToken} />}
      {tab === 'phone' && <PhonePanel agent={agent} hookToken={hookToken} />}
    </section>
  )
}

// ---------------- Email ----------------
function EmailPanel({ agent, hookToken }) {
  const { t } = useTranslation('identity')
  const [cfg, setCfg] = useState(null)
  const [recent, setRecent] = useState([])
  const [busy, setBusy] = useState('')
  const [shared, setShared] = useState('')
  useEffect(() => { api('/api/identity/addresses').then((a) => setShared(a[agent.id] || '')).catch(() => {}) }, [agent.id])
  const load = () => {
    api(`/api/identity/email/${agent.id}`).then(setCfg)
    api(`/api/identity/email/${agent.id}/recent`).then(setRecent)
  }
  useEffect(load, [agent.id])
  if (!cfg) return null
  const set = (patch) => setCfg({ ...cfg, ...patch })
  const setSmtp = (patch) => setCfg({ ...cfg, smtp: { ...(cfg.smtp || {}), ...patch } })
  const setImap = (patch) => setCfg({ ...cfg, imap: { ...(cfg.imap || {}), ...patch } })

  const save = async () => {
    await api(`/api/identity/email/${agent.id}`, { method: 'PUT', body: cfg })
    toast(t('email.saved'))
    load()
  }
  const saveSecret = async (which, value) => {
    if (!value) return null
    const name = `EMAIL_${which}_${agent.id}`.toUpperCase()
    await api('/api/vault', { method: 'POST', body: { name, value } })
    return `{{vault:${name}}}`
  }
  const saveSmtpPass = async (v) => setSmtp({ password: (await saveSecret('SMTP', v)) || cfg.smtp?.password })
  const saveImapPass = async (v) => setImap({ password: (await saveSecret('IMAP', v)) || cfg.imap?.password })

  const test = async (kind) => {
    setBusy(kind)
    try {
      const r = await api(`/api/identity/email/${agent.id}/test-${kind}`, { method: 'POST', body: {} })
      toast(kind === 'smtp' ? t('email.testSent', { to: r.sent_to }) : t('email.imapWorks', { count: r.messages }))
    } catch (e) {
      toast('❌ ' + e.message)
    }
    setBusy('')
  }

  const hook = `${location.origin}/hook/${hookToken}/email/${agent.id}`

  return (
    <div>
      {shared && !cfg.address && (
        <p className="small wz-ok">✓ <Trans t={t} i18nKey="email.sharedAddress" values={{ name: agent.name, address: shared }} components={{ b: <b /> }} /></p>
      )}
      {!shared && !cfg.address && <p className="muted small">{t('email.noMailboxYet')} <a href="#/settings/identity">{t('email.setUpMailbox')}</a></p>}
      <details open={!!cfg.address}>
      <summary className="muted small">{t('email.ownSettings')}</summary>
      <div className="row gap6 wrap">
        <input className="input" placeholder="agent@you.example" value={cfg.address || ''} onChange={(e) => set({ address: e.target.value })} />
        <input className="input" placeholder={t('fields.displayName')} value={cfg.display_name || ''} onChange={(e) => set({ display_name: e.target.value })} style={{ maxWidth: 180 }} />
      </div>

      <h4>{t('email.send')}</h4>
      <div className="row gap6 wrap">
        <input className="input" placeholder={t('mailbox.smtpHost')} value={cfg.smtp?.host || ''} onChange={(e) => setSmtp({ host: e.target.value })} />
        <input className="input" placeholder={t('fields.port')} type="number" value={cfg.smtp?.port || ''} onChange={(e) => setSmtp({ port: Number(e.target.value) })} style={{ maxWidth: 90 }} />
        <input className="input" placeholder={t('fields.username')} value={cfg.smtp?.user || ''} onChange={(e) => setSmtp({ user: e.target.value })} />
        <input className="input" type="password" placeholder={t('fields.password')} onBlur={(e) => saveSmtpPass(e.target.value)} />
        <label className="row gap6 small"><input type="checkbox" checked={cfg.smtp?.starttls ?? true} onChange={(e) => setSmtp({ starttls: e.target.checked })} /> STARTTLS</label>
      </div>

      <h4>{t('email.receive')}</h4>
      <div className="seg" style={{ maxWidth: 260 }}>
        <button className={cfg.mode !== 'webhook' ? 'on' : ''} onClick={() => set({ mode: 'imap' })}>{t('email.imapPolling')}</button>
        <button className={cfg.mode === 'webhook' ? 'on' : ''} onClick={() => set({ mode: 'webhook' })}>{t('email.webhook')}</button>
      </div>
      {cfg.mode !== 'webhook' ? (
        <div className="row gap6 wrap mt8">
          <input className="input" placeholder={t('mailbox.imapHost')} value={cfg.imap?.host || ''} onChange={(e) => setImap({ host: e.target.value })} />
          <input className="input" placeholder={t('fields.port')} type="number" value={cfg.imap?.port || ''} onChange={(e) => setImap({ port: Number(e.target.value) })} style={{ maxWidth: 90 }} />
          <input className="input" placeholder={t('fields.username')} value={cfg.imap?.user || ''} onChange={(e) => setImap({ user: e.target.value })} />
          <input className="input" type="password" placeholder={t('fields.password')} onBlur={(e) => saveImapPass(e.target.value)} />
          <input className="input" placeholder={t('fields.folder')} value={cfg.imap?.folder || ''} onChange={(e) => setImap({ folder: e.target.value })} style={{ maxWidth: 130 }} />
        </div>
      ) : (
        <div className="mt8">
          <p className="muted small">{t('email.pointAt')}</p>
          <div className="row gap6">
            <pre className="code" style={{ flex: 1 }}>{hook}</pre>
            <button className="btn ghost sm" onClick={() => { navigator.clipboard?.writeText(hook); toast(t('common:copied')) }}>{t('common:copy')}</button>
          </div>
          <details>
            <summary className="small muted">{t('email.workerSnippet')}</summary>
            <div className="row gap6" style={{ alignItems: 'flex-start' }}>
              <pre className="code" style={{ flex: 1 }}>{WORKER_SNIPPET.replace('HOOK_URL', `${location.origin}/hook/${hookToken}/email`).replace('AGENT_NAME', agent.name.toLowerCase())}</pre>
              <button className="btn ghost sm" onClick={() => {
                navigator.clipboard?.writeText(WORKER_SNIPPET.replace('HOOK_URL', `${location.origin}/hook/${hookToken}/email`).replace('AGENT_NAME', agent.name.toLowerCase()))
                toast(t('common:copied'))
              }}>{t('common:copy')}</button>
            </div>
          </details>
        </div>
      )}

      <label className="row gap6 small mt8">
        <input type="checkbox" checked={cfg.show_otp || false} onChange={(e) => set({ show_otp: e.target.checked })} />
        {t('email.showOtp')}
      </label>

      <div className="row gap6 wrap mt8">
        <button className="btn" onClick={save}>{t('common:save')}</button>
        <button className="btn ghost sm" disabled={busy === 'smtp'} onClick={() => test('smtp')}>{t('email.testSend')}</button>
        {cfg.mode !== 'webhook' && <button className="btn ghost sm" disabled={busy === 'imap'} onClick={() => test('imap')}>{t('email.testImap')}</button>}
      </div>

      </details>

      <Recent rows={recent} render={(r) => `${r.direction === 'out' ? '→' : '←'} ${r.subject || t('email.noSubject')} · ${r.direction === 'out' ? r.to_addr : r.from_addr}`} />
    </div>
  )
}

// ---------------- Calendar ----------------
function PhonePanel({ agent, hookToken }) {
  const { t } = useTranslation('identity')
  const [cfg, setCfg] = useState(null)
  const [recent, setRecent] = useState([])
  const [numbers, setNumbers] = useState(null) // from the shared account (Settings → Agent identities)
  const [pub, setPub] = useState(null)
  useEffect(() => { api('/api/identity/phone_account/public_url').then(setPub).catch(() => {}) }, [])
  const load = () => {
    api(`/api/identity/phone/${agent.id}`).then(setCfg)
    api(`/api/identity/phone/${agent.id}/recent`).then(setRecent)
    api('/api/identity/phone_account/numbers').then(setNumbers).catch(() => setNumbers(null))
  }
  useEffect(load, [agent.id])
  if (!cfg) return null
  const set = (patch) => setCfg({ ...cfg, ...patch })
  const saveToken = async (v) => {
    if (!v) return
    const name = `TWILIO_TOKEN_${agent.id}`.toUpperCase()
    await api('/api/vault', { method: 'POST', body: { name, value: v } })
    setCfg({ ...cfg, auth_token: `{{vault:${name}}}` })
  }
  const save = async () => {
    await api(`/api/identity/phone/${agent.id}`, { method: 'PUT', body: cfg })
    toast(t('phone.saved'))
    load()
  }
  const test = async () => {
    try {
      const r = await api(`/api/identity/phone/${agent.id}/test`, { method: 'POST' })
      toast(t('phone.connected', { account: r.account }))
    } catch (e) {
      toast('❌ ' + e.message)
    }
  }
  const slug = agent.id // id, not name: the webhook keeps working after a rename
  const smsHook = `${location.origin}/hook/${hookToken}/twilio/sms/${slug}`
  const voiceHook = `${location.origin}/hook/${hookToken}/twilio/voice/${slug}`

  const mine = numbers?.find((n) => n.agent_id === agent.id)
  const pick = async (n) => {
    try {
      const r = n
        ? await api(`/api/identity/phone/${agent.id}/assign`, { method: 'POST', body: { number: n.number, sid: n.sid } })
        : await api(`/api/identity/phone/${agent.id}/unassign`, { method: 'POST' })
      if (n) toast(r.wired ? t('twilio.wiredToast', { name: agent.name, number: n.number }) : t('twilio.notWiredToast', { name: agent.name }))
    } catch (e) {
      const { code, message } = errOf(e)
      toast('❌ ' + t(`twilio.err.${code}`, { defaultValue: message || t('twilio.err.twilio') }))
    }
    load()
  }

  if (numbers)
    return (
      <div>
        <p className="muted small">{t('phone.pick', { name: agent.name })}</p>
        <div className="row gap6 wrap">
          {numbers.map((n) => (
            <button key={n.number} className={`chip ${cfg.from_number === n.number ? 'on' : ''}`} disabled={n.agent_id && n.agent_id !== agent.id}
              onClick={() => pick(cfg.from_number === n.number ? null : n)}>
              {n.number}{n.agent_id && n.agent_id !== agent.id ? t('phone.taken') : ''}
            </button>
          ))}
          {numbers.length === 0 && <span className="muted small">{t('phone.noNumbers')}</span>}
        </div>
        {mine && (mine.wired
          ? <p className="small mt8 wz-ok">✓ {t('twilio.goesTo', { name: agent.name })}</p>
          : (
            <div className="mt8">
              <p className="small wz-warn">{t('twilio.notPointed', { name: agent.name })} {publicUrlText(t, pub)}</p>
              <button className="btn ghost sm" onClick={() => pick(mine)}>{t('twilio.fix')}</button>
              <details className="mt8">
                <summary className="small muted">{t('phone.webhooksHelp')}</summary>
                <pre className="code">{`Messaging → A message comes in:  ${smsHook}\nVoice → A call comes in:         ${voiceHook}`}</pre>
              </details>
            </div>
          ))}
        <Recent rows={recent} render={(r) => `${r.kind === 'sms' ? '💬' : '📞'} ${r.direction === 'out' ? '→' : '←'} ${r.direction === 'out' ? r.to_number : r.from_number} · ${r.status}`} />
      </div>
    )

  return (
    <div>
      <div className="row gap6 wrap">
        <input className="input" placeholder={t('fields.accountSid')} value={cfg.account_sid || ''} onChange={(e) => set({ account_sid: e.target.value })} />
        <input className="input" type="password" placeholder={t('fields.authToken')} onBlur={(e) => saveToken(e.target.value)} />
        <input className="input" placeholder="+1 555 000 0000" value={cfg.from_number || ''} onChange={(e) => set({ from_number: e.target.value })} style={{ maxWidth: 170 }} />
      </div>
      <div className="row gap6 wrap mt8">
        <button className="btn" onClick={save}>{t('common:save')}</button>
        <button className="btn ghost sm" onClick={test}>{t('phone.test')}</button>
      </div>
      <p className="muted small mt8">{t('phone.webhooksHelp')}</p>
      <pre className="code">{`Messaging → A message comes in:  ${smsHook}\nVoice → A call comes in:         ${voiceHook}`}</pre>
      <Recent rows={recent} render={(r) => `${r.kind === 'sms' ? '💬' : '📞'} ${r.direction === 'out' ? '→' : '←'} ${r.direction === 'out' ? r.to_number : r.from_number} · ${r.status}`} />
    </div>
  )
}

// ---------------- shared ----------------
function Recent({ rows, render }) {
  const { t } = useTranslation('identity')
  return (
    <div className="mt8">
      <h4>{t('recent.title')}</h4>
      {rows.length === 0 && <p className="muted small">{t('recent.empty')}</p>}
      {rows.map((r) => (
        <div key={r.id} className="small id-recent-row">{render(r)}</div>
      ))}
    </div>
  )
}
