// Shared bits for the identity setup wizards (Settings → Agent identities, and each
// agent's Identity tab).

// Server errors come back as the raw response text; ours are {"detail": {code, message}}
// or {"detail": "text"}. Returns {code, message}.
export function errOf(e) {
  const raw = e?.message || String(e)
  try {
    const d = JSON.parse(raw).detail
    if (d && typeof d === 'object') return { code: d.code || 'other', message: d.message || '' }
    return { code: 'other', message: String(d ?? raw) }
  } catch {
    return { code: 'other', message: raw }
  }
}

// Pick the en / zh text out of a {en, zh} object.
export const pick = (obj, lang) => (obj ? (String(lang).startsWith('zh') ? obj.zh || obj.en : obj.en) : '')

// Mail providers: server settings the wizard fills in, and where to get an app password.
// plus: whether name+tag@ addresses are delivered (false = warn, null = not sure).
export const PROVIDERS = {
  gmail: {
    domains: ['gmail.com', 'googlemail.com'],
    smtp: { host: 'smtp.gmail.com', port: 587, starttls: true },
    imap: { host: 'imap.gmail.com', port: 993, ssl: true },
    plus: true,
    links: [
      ['twoStep', 'https://myaccount.google.com/security'],
      ['appPassword', 'https://myaccount.google.com/apppasswords'],
    ],
  },
  outlook: {
    domains: ['outlook.com', 'hotmail.com', 'live.com', 'msn.com'],
    smtp: { host: 'smtp-mail.outlook.com', port: 587, starttls: true },
    imap: { host: 'outlook.office365.com', port: 993, ssl: true },
    plus: true,
    warn: true,
    links: [['appPassword', 'https://account.live.com/proofs/AppPassword']],
  },
  icloud: {
    domains: ['icloud.com', 'me.com', 'mac.com'],
    smtp: { host: 'smtp.mail.me.com', port: 587, starttls: true },
    imap: { host: 'imap.mail.me.com', port: 993, ssl: true },
    plus: null,
    links: [['appleAccount', 'https://account.apple.com']],
  },
  fastmail: {
    domains: ['fastmail.com', 'fastmail.fm'],
    smtp: { host: 'smtp.fastmail.com', port: 465, starttls: false },
    imap: { host: 'imap.fastmail.com', port: 993, ssl: true },
    plus: true,
    links: [['help', 'https://www.fastmail.help/hc/en-us/articles/360058752854-App-passwords']],
  },
  qq: {
    domains: ['qq.com', 'foxmail.com'],
    smtp: { host: 'smtp.qq.com', port: 465, starttls: false },
    imap: { host: 'imap.qq.com', port: 993, ssl: true },
    plus: false,
    links: [['openMail', 'https://mail.qq.com']],
  },
  netease: {
    domains: ['163.com'],
    smtp: { host: 'smtp.163.com', port: 465, starttls: false },
    imap: { host: 'imap.163.com', port: 993, ssl: true },
    plus: false,
    links: [['openMail', 'https://mail.163.com']],
  },
  other: { domains: [], plus: null, links: [] },
}

export function providerFor(address) {
  const d = (address.split('@')[1] || '').toLowerCase()
  return Object.keys(PROVIDERS).find((k) => PROVIDERS[k].domains.includes(d)) || ''
}

// Countries people most often buy Twilio numbers in; any other ISO code can be typed.
export const COUNTRIES = ['US', 'CA', 'GB', 'AU', 'IE', 'DE', 'FR', 'NL', 'SE', 'ES', 'IT', 'CH', 'BE', 'AT', 'PL', 'HK', 'SG', 'JP']

export function countryName(code, lang) {
  try {
    return new Intl.DisplayNames([String(lang).startsWith('zh') ? 'zh-CN' : 'en'], { type: 'region' }).of(code)
  } catch {
    return code
  }
}

// "Texts reach your agents through <url>" / why they can't yet.
export function publicUrlText(t, pub) {
  if (!pub) return ''
  return pub.url ? t('twilio.publicOk', { url: pub.url }) : t(`twilio.public.${pub.reason || 'no_public_url'}`)
}
