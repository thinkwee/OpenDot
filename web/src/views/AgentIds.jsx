import { useEffect, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { api, toast, useStore } from '../store'
import Mascot, { animalFor } from '../components/Mascot'
import { MyCalendars } from './Calendar'
import { COUNTRIES, PROVIDERS, countryName, errOf, providerFor, publicUrlText } from './idWizard'
import './Identity.css'

// Settings → Agent identities: one mailbox that gives every agent its own address,
// one phone account they pick numbers from, and how often they may nudge you.
export default function AgentIds() {
  return (
    <div className="cards">
      <Mailbox />
      <MyCalendars />
      <PhoneAccount />
      <Budget />
    </div>
  )
}

const vault = (name, value) => api('/api/vault', { method: 'POST', body: { name, value } })

// ---------------- the shared mailbox: pick a provider → app password → Test → Save ----------------
function Mailbox() {
  const { t } = useTranslation('identity')
  const { agents } = useStore()
  const [cfg, setCfg] = useState(null)
  const [pw, setPw] = useState('')
  const [addrs, setAddrs] = useState({})
  const [sendTest, setSendTest] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState(null)
  useEffect(() => {
    api('/api/identity/mailbox').then((c) => setCfg({ style: 'plus', ...c, provider: c.provider || providerFor(c.address || '') || (c.address ? 'other' : '') }))
    api('/api/identity/addresses').then(setAddrs)
  }, [])
  if (!cfg) return <div className="card">…</div>
  const prov = cfg.provider
  const P = PROVIDERS[prov]

  const withPreset = (next, key) => {
    const p = PROVIDERS[key]
    if (!p?.smtp) return { ...next, provider: key }
    return {
      ...next,
      provider: key,
      smtp: { ...(next.smtp || {}), ...p.smtp, user: next.address || '' },
      imap: { folder: 'INBOX', ...(next.imap || {}), ...p.imap, user: next.address || '' },
    }
  }
  const choose = (key) => { setCfg(withPreset(cfg, key)); setResult(null) }
  const setAddress = (address) => {
    let next = { ...cfg, address, smtp: { ...(cfg.smtp || {}), user: address }, imap: { ...(cfg.imap || {}), user: address } }
    const guessed = providerFor(address)
    if (guessed && guessed !== cfg.provider && (!cfg.provider || cfg.provider === 'other')) next = withPreset(next, guessed)
    setCfg(next)
    setResult(null)
  }
  const set = (k, v) => setCfg({ ...cfg, [k]: v })
  const setSrv = (which, patch) => { setCfg({ ...cfg, [which]: { ...(cfg[which] || {}), ...patch } }); setResult(null) }

  const test = async () => {
    setBusy(true)
    setResult(null)
    try {
      setResult(await api('/api/identity/mailbox/test', {
        method: 'POST',
        body: { address: cfg.address, password: pw || undefined, smtp: cfg.smtp, imap: cfg.imap, send_test: sendTest },
      }))
    } catch (e) {
      setResult({ ok: false, imap: { ok: false, code: 'other', detail: errOf(e).message }, smtp: { ok: false, code: 'other', detail: '' } })
    }
    setBusy(false)
  }
  const save = async () => {
    const next = { ...cfg }
    if (pw) {
      await vault('AGENT_MAILBOX_PASSWORD', pw)
      next.smtp = { ...(next.smtp || {}), password: '{{vault:AGENT_MAILBOX_PASSWORD}}' }
      next.imap = { ...(next.imap || {}), password: '{{vault:AGENT_MAILBOX_PASSWORD}}' }
    }
    const r = await api('/api/identity/mailbox', { method: 'PUT', body: next })
    setCfg(next)
    setAddrs(r.addresses || {})
    setPw('')
    toast(t('mailbox.saved'))
  }
  const steps = prov ? t(`providers.${prov}.steps`, { returnObjects: true }) : []
  const errText = (r) => {
    const base = t(`err.${r.code}`, { defaultValue: t('err.other') })
    const hint = r.code === 'auth' && prov ? t(`providers.${prov}.authHint`, { defaultValue: '' }) : ''
    return hint ? `${base} ${hint}` : base
  }

  return (
    <div className="card" style={{ gridColumn: '1 / -1' }}>
      <h3>{t('mailbox.title')}</h3>
      <p className="muted small">
        <Trans t={t} i18nKey="mailbox.body" components={{ i: <i /> }} />
      </p>
      <div className="note small">
        <Trans t={t} i18nKey="mailbox.how" components={{ b: <b />, br: <br /> }} />
      </div>

      <p className="small wz-q">{t('wizard.pickProvider')}</p>
      <div className="row gap6 wrap">
        {Object.keys(PROVIDERS).map((k) => (
          <button key={k} className={`chip ${prov === k ? 'on' : ''}`} onClick={() => choose(k)}>{t(`providers.${k}.name`)}</button>
        ))}
      </div>

      {prov && (
        <div className="wz-box mt8">
          {Array.isArray(steps) && steps.length > 0 && (
            <ol className="wz-steps small">
              {steps.map((s, i) => <li key={i}>{s}</li>)}
            </ol>
          )}
          {P.links.length > 0 && (
            <div className="row gap6 wrap">
              {P.links.map(([k, url]) => (
                <a key={k} className="btn ghost sm" href={url} target="_blank" rel="noreferrer">{t(`links.${k}`)} ↗</a>
              ))}
            </div>
          )}
          {P.warn && <p className="small wz-warn">{t(`providers.${prov}.warn`)}</p>}
          {P.plus === false && cfg.style !== 'domain' && <p className="small wz-warn">{t('wizard.noPlus', { provider: t(`providers.${prov}.name`) })}</p>}
        </div>
      )}

      <div className="row gap8 wrap mt8">
        <input className="input" style={{ flex: '1 1 240px' }} placeholder={t('wizard.addressPh')} value={cfg.address || ''} onChange={(e) => setAddress(e.target.value.trim())} />
        <input className="input" style={{ flex: '1 1 200px' }} type="password" autoComplete="new-password" placeholder={cfg.smtp?.password ? t('mailbox.passwordSaved') : t('mailbox.appPassword')} value={pw} onChange={(e) => { setPw(e.target.value); setResult(null) }} />
      </div>
      <div className="seg mt8">
        <button className={cfg.style !== 'domain' ? 'on' : ''} onClick={() => set('style', 'plus')}>{t('mailbox.stylePlus')}</button>
        <button className={cfg.style === 'domain' ? 'on' : ''} onClick={() => set('style', 'domain')}>{t('mailbox.styleDomain')}</button>
      </div>
      {cfg.style === 'domain' && (
        <input className="input mt8" placeholder="agents.example.com" value={cfg.domain || ''} onChange={(e) => set('domain', e.target.value)} />
      )}
      <details className="mt8" open={prov === 'other'}>
        <summary className="muted small">{t('mailbox.serverSettings')}</summary>
        {['imap', 'smtp'].map((w) => (
          <div key={w} className="row gap6 wrap mt8 wz-srv">
            <b className="small">{w === 'imap' ? t('wizard.receiving') : t('wizard.sending')}</b>
            <input className="input" placeholder={t(w === 'imap' ? 'mailbox.imapHost' : 'mailbox.smtpHost')} value={cfg[w]?.host || ''} onChange={(e) => setSrv(w, { host: e.target.value.trim() })} />
            <input className="input" type="number" placeholder={t('fields.port')} style={{ maxWidth: 90 }} value={cfg[w]?.port || ''}
              onChange={(e) => setSrv(w, { port: Number(e.target.value) || '' })} />
            <input className="input" placeholder={t('fields.username')} value={cfg[w]?.user || ''} onChange={(e) => setSrv(w, { user: e.target.value.trim() })} />
            {w === 'imap' ? (
              <label className="row gap6 small"><input type="checkbox" checked={cfg.imap?.ssl ?? true} onChange={(e) => setSrv('imap', { ssl: e.target.checked })} /> SSL/TLS</label>
            ) : (
              <select className="input" style={{ maxWidth: 150 }} value={(cfg.smtp?.starttls ?? (Number(cfg.smtp?.port) !== 465)) ? 'starttls' : 'ssl'}
                onChange={(e) => setSrv('smtp', { starttls: e.target.value === 'starttls' })}>
                <option value="starttls">STARTTLS</option>
                <option value="ssl">SSL/TLS</option>
              </select>
            )}
          </div>
        ))}
      </details>

      <label className="row gap6 small mt8">
        <input type="checkbox" checked={sendTest} onChange={(e) => setSendTest(e.target.checked)} />
        {t('wizard.sendTest')}
      </label>
      <div className="row gap8 mt8 wrap">
        <button className="btn ghost" disabled={busy || !cfg.address || !cfg.smtp?.host || !cfg.imap?.host} onClick={test}>{busy ? t('wizard.testing') : t('wizard.test')}</button>
        <button className="btn" disabled={!cfg.address} onClick={save}>{t('common:save')}</button>
      </div>

      {result && (
        <div className="wz-result mt8">
          {[['imap', t('wizard.receiving')], ['smtp', t('wizard.sending')]].map(([k, label]) => {
            const r = result[k] || {}
            return (
              <div key={k} className={`wz-line ${r.ok ? 'ok' : 'bad'}`}>
                <b>{r.ok ? '✓' : '✕'} {label}</b>{' '}
                <span>
                  {r.ok
                    ? k === 'imap' ? t('wizard.imapOk', { count: r.messages ?? 0 }) : r.sent_to ? t('wizard.smtpSent', { to: r.sent_to }) : t('wizard.smtpOk')
                    : errText(r)}
                </span>
                {!r.ok && r.detail && <small className="muted wz-detail">{t('wizard.serverSaid')} {r.detail}</small>}
              </div>
            )
          })}
          {result.ok && <p className="small"><b>{t('wizard.allGood')}</b></p>}
        </div>
      )}

      {Object.values(addrs).some(Boolean) && (
        <>
          <p className="small mt8"><b>{t('wizard.addresses')}</b></p>
          <div className="idc-list">
            {agents.map((a) => addrs[a.id] && (
              <div key={a.id} className="idc-item">
                <Mascot color={a.color} animal={animalFor(a)} size={30} bubble={false} />
                <div className="grow"><b>{a.name}</b><small>{addrs[a.id]}</small></div>
              </div>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

// ---------------- phone numbers: Twilio account → numbers → who gets which ----------------
function PhoneAccount() {
  const { t, i18n } = useTranslation('identity')
  const { agents } = useStore()
  const [acc, setAcc] = useState(null)
  const [sid, setSid] = useState('')
  const [tok, setTok] = useState('')
  const [conn, setConn] = useState(null) // {name, type} once the account answers
  const [editing, setEditing] = useState(false)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState('')
  const [numbers, setNumbers] = useState([])
  const [pub, setPub] = useState(null)
  const [q, setQ] = useState({ country: 'US', type: 'local', area: '', contains: '' })
  const [found, setFound] = useState(null)
  const [buyFor, setBuyFor] = useState('')

  const twErr = (e) => {
    const { code, message } = errOf(e)
    return `${t(`twilio.err.${code}`, { defaultValue: t('twilio.err.twilio') })}${message && code !== 'auth' ? ` (${message})` : ''}`
  }
  const loadNumbers = () => api('/api/identity/phone_account/numbers').then(setNumbers).catch(() => setNumbers([]))
  useEffect(() => {
    api('/api/identity/phone_account').then((a) => {
      setAcc(a)
      setSid(a.account_sid || '')
      if (a.account_sid && a.auth_token) {
        api('/api/identity/phone_account/test', { method: 'POST', body: {} })
          .then((r) => { setConn(r); loadNumbers() })
          .catch((e) => { setErr(twErr(e)); setEditing(true) })
      } else setEditing(true)
    })
    api('/api/identity/phone_account/public_url').then(setPub).catch(() => {})
  }, [])
  if (!acc) return <div className="card">…</div>

  const connect = async () => {
    setBusy('connect')
    setErr('')
    try {
      const r = await api('/api/identity/phone_account/test', { method: 'POST', body: { account_sid: sid.trim(), auth_token: tok.trim() || undefined } })
      const next = { ...acc, account_sid: sid.trim() }
      if (tok.trim()) {
        await vault('TWILIO_AUTH_TOKEN', tok.trim())
        next.auth_token = '{{vault:TWILIO_AUTH_TOKEN}}'
      }
      await api('/api/identity/phone_account', { method: 'PUT', body: next })
      setAcc(next)
      setTok('')
      setConn(r)
      setEditing(false)
      toast(t('twilio.connected', { name: r.name }))
      loadNumbers()
    } catch (e) {
      setErr(twErr(e))
    }
    setBusy('')
  }
  const assign = async (n, agentId) => {
    setBusy(n.number)
    try {
      if (!agentId) {
        await api(`/api/identity/phone/${n.agent_id}/unassign`, { method: 'POST' })
      } else {
        const r = await api(`/api/identity/phone/${agentId}/assign`, { method: 'POST', body: { number: n.number, sid: n.sid } })
        const name = agents.find((a) => a.id === agentId)?.name || ''
        toast(r.wired ? t('twilio.wiredToast', { name, number: n.number }) : t('twilio.notWiredToast', { name }))
      }
    } catch (e) {
      toast('❌ ' + twErr(e))
    }
    setBusy('')
    loadNumbers()
  }
  const search = async () => {
    setBusy('search')
    setFound(null)
    try {
      const qs = new URLSearchParams({ country: q.country, type: q.type, area_code: q.area, contains: q.contains })
      setFound(await api(`/api/identity/phone_account/available?${qs}`))
      const free = agents.find((a) => !numbers.some((n) => n.agent_id === a.id))
      setBuyFor(free?.id || '')
    } catch (e) {
      toast('❌ ' + twErr(e))
    }
    setBusy('')
  }
  const buy = async (n) => {
    const price = found?.price?.monthly ? t('twilio.pricePer', { price: found.price.monthly, unit: found.price.unit }) : t('twilio.priceUnknown')
    if (!window.confirm(t('twilio.confirmBuy', { number: n.number, price }))) return
    setBusy(n.number)
    try {
      const r = await api('/api/identity/phone_account/buy', { method: 'POST', body: { phone_number: n.number, confirm: true, agent_id: buyFor || undefined } })
      const name = agents.find((a) => a.id === buyFor)?.name
      toast(name ? (r.wired ? t('twilio.wiredToast', { name, number: r.number }) : t('twilio.notWiredToast', { name })) : t('twilio.bought', { number: r.number }))
      setFound(null)
      loadNumbers()
    } catch (e) {
      toast('❌ ' + twErr(e))
    }
    setBusy('')
  }
  const nameOf = (id) => agents.find((a) => a.id === id)?.name || '?'

  return (
    <div className="card" style={{ gridColumn: '1 / -1' }}>
      <h3>{t('phoneAccount.title')}</h3>
      <p className="muted small">{t('phoneAccount.body')}</p>

      {conn && !editing ? (
        <div className="row between wrap gap6">
          <span className="pill ok">✓ {t('twilio.connectedAs', { name: conn.name })}</span>
          <button className="btn ghost sm" onClick={() => setEditing(true)}>{t('twilio.change')}</button>
        </div>
      ) : (
        <>
          <ol className="wz-steps small">
            {[].concat(t('twilio.steps', { returnObjects: true })).map((s, i) => <li key={i}>{s}</li>)}
          </ol>
          <div className="row gap6 wrap">
            <a className="btn ghost sm" href="https://www.twilio.com/try-twilio" target="_blank" rel="noreferrer">{t('twilio.signUp')} ↗</a>
            <a className="btn ghost sm" href="https://console.twilio.com" target="_blank" rel="noreferrer">{t('twilio.console')} ↗</a>
          </div>
          <div className="row gap8 wrap mt8">
            <input className="input" style={{ flex: '1 1 220px' }} placeholder={t('fields.accountSid')} value={sid} onChange={(e) => setSid(e.target.value)} />
            <input className="input" style={{ flex: '1 1 200px' }} type="password" autoComplete="new-password" placeholder={acc.auth_token ? t('phoneAccount.tokenSaved') : t('fields.authToken')} value={tok} onChange={(e) => setTok(e.target.value)} />
            <button className="btn" disabled={busy === 'connect' || !sid.trim() || (!tok.trim() && !acc.auth_token)} onClick={connect}>{busy === 'connect' ? t('wizard.testing') : t('twilio.testSave')}</button>
          </div>
          {err && <p className="small wz-warn">{err}</p>}
        </>
      )}

      {conn && conn.type === 'Trial' && <p className="small muted mt8">{t('twilio.trialNote')}</p>}

      {conn && (
        <>
          <p className={`small mt8 ${pub?.url ? 'muted' : 'wz-warn'}`}>{publicUrlText(t, pub)}</p>
          <h4 className="wz-h">{t('twilio.yourNumbers')}</h4>
          {numbers.length === 0 && <p className="muted small">{t('twilio.noNumbers')}</p>}
          {numbers.map((n) => (
            <div key={n.number} className="wz-num">
              <div className="grow">
                <b>{n.number}</b> <small className="muted">{[n.sms && t('twilio.sms'), n.voice && t('twilio.voice')].filter(Boolean).join(' · ')}</small>
                <div className="small">
                  {n.agent_id
                    ? n.wired
                      ? <span className="wz-ok">✓ {t('twilio.goesTo', { name: nameOf(n.agent_id) })}</span>
                      : <span className="wz-warn">{t('twilio.notPointed', { name: nameOf(n.agent_id) })} <button className="btn ghost sm" onClick={() => assign(n, n.agent_id)}>{t('twilio.fix')}</button></span>
                    : <span className="muted">{t('twilio.nobody')}</span>}
                </div>
              </div>
              <select className="input" style={{ maxWidth: 170 }} disabled={busy === n.number} value={n.agent_id || ''} onChange={(e) => assign(n, e.target.value)}>
                <option value="">{t('twilio.nobodyOpt')}</option>
                {agents.map((a) => {
                  const other = numbers.find((m) => m.agent_id === a.id && m.number !== n.number)
                  return <option key={a.id} value={a.id} disabled={!!other}>{a.name}{other ? ` (${other.number})` : ''}</option>
                })}
              </select>
            </div>
          ))}

          <details className="mt8" open={numbers.length === 0}>
            <summary className="small"><b>{t('twilio.getNumber')}</b></summary>
            <div className="row gap6 wrap mt8">
              <select className="input" style={{ maxWidth: 190 }} value={q.country} onChange={(e) => setQ({ ...q, country: e.target.value })}>
                {COUNTRIES.map((c) => <option key={c} value={c}>{countryName(c, i18n.language)}</option>)}
              </select>
              <div className="seg" style={{ margin: 0, flex: '1 1 240px' }}>
                {['local', 'mobile', 'tollfree'].map((k) => (
                  <button key={k} className={q.type === k ? 'on' : ''} onClick={() => setQ({ ...q, type: k })}>{t(`twilio.type.${k}`)}</button>
                ))}
              </div>
            </div>
            <div className="row gap6 wrap mt8">
              {['US', 'CA'].includes(q.country) && (
                <input className="input" style={{ maxWidth: 130 }} inputMode="numeric" placeholder={t('twilio.areaCode')} value={q.area} onChange={(e) => setQ({ ...q, area: e.target.value })} />
              )}
              <input className="input" style={{ maxWidth: 170 }} placeholder={t('twilio.contains')} value={q.contains} onChange={(e) => setQ({ ...q, contains: e.target.value })} />
              <button className="btn ghost" disabled={busy === 'search'} onClick={search}>{busy === 'search' ? t('twilio.searching') : t('twilio.search')}</button>
            </div>
            {found && (
              <div className="mt8">
                <p className="small">
                  {found.price?.monthly ? t('twilio.priceLine', { price: found.price.monthly, unit: found.price.unit }) : t('twilio.priceUnknownLine')}
                </p>
                {found.numbers.length === 0 && <p className="muted small">{t('twilio.noneFound')}</p>}
                {found.numbers.length > 0 && (
                  <label className="row gap6 small mb8">
                    {t('twilio.giveTo')}
                    <select className="input" style={{ maxWidth: 170 }} value={buyFor} onChange={(e) => setBuyFor(e.target.value)}>
                      <option value="">{t('twilio.nobodyYet')}</option>
                      {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                    </select>
                  </label>
                )}
                {found.numbers.map((n) => (
                  <div key={n.number} className="wz-num">
                    <div className="grow">
                      <b>{n.name || n.number}</b>{' '}
                      <small className="muted">{[n.locality, n.region].filter(Boolean).join(', ')}{' · '}{[n.sms && t('twilio.sms'), n.voice && t('twilio.voice')].filter(Boolean).join(' · ')}</small>
                      {n.address_required && <div className="small wz-warn">{t('twilio.needsAddress')}</div>}
                    </div>
                    <button className="btn sm" disabled={!!busy} onClick={() => buy(n)}>{busy === n.number ? '…' : t('twilio.buy')}</button>
                  </div>
                ))}
              </div>
            )}
          </details>
        </>
      )}
    </div>
  )
}

function Budget() {
  const { t } = useTranslation('identity')
  const [b, setB] = useState(null)
  useEffect(() => {
    api('/api/push/budget').then(setB).catch(() => {})
  }, [])
  return (
    <div className="card">
      <h3>{t('budget.title')}</h3>
      <p className="muted small">
        <Trans t={t} i18nKey="budget.body" values={{ limit: b?.limit ?? 4, from: b?.quiet?.[0] ?? 22, to: b?.quiet?.[1] ?? 8 }} components={{ b: <b /> }} />
      </p>
      {b && <p className="small"><b>{t('budget.today')}</b> {t('budget.todayCounts', { used: b.used, held: b.held })}</p>}
      <p className="muted small"><Trans t={t} i18nKey="budget.changeWith" components={{ c1: <code />, c2: <code />, c3: <code /> }} /></p>
    </div>
  )
}
