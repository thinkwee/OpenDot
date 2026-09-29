// Bilingual UI (English / 中文) with i18next.
// Strings live in src/locales/<lang>/<namespace>.json — one namespace per view
// (chats, agent, settings, …) plus `common` for shared words. Use them with
//   const { t } = useTranslation('chats');  t('newAgent.title')  t('common:save')
// The chosen language is remembered per browser and sent to the server, so what
// agents send on their own (pushes, templates, approval wording) matches it.
import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'

const files = import.meta.glob('./locales/*/*.json', { eager: true })
const resources = {}
for (const [path, mod] of Object.entries(files)) {
  const [, lang, ns] = path.match(/locales\/(\w+)\/([\w-]+)\.json$/)
  ;(resources[lang] ||= {})[ns] = mod.default || mod
}

export const LANGS = [
  ['en', 'English'],
  ['zh', '中文'],
]

function initial() {
  try {
    const saved = localStorage.getItem('dot_lang')
    if (saved === 'en' || saved === 'zh') return saved
  } catch { /* private mode */ }
  return (navigator.language || '').toLowerCase().startsWith('zh') ? 'zh' : 'en'
}

i18n.use(initReactI18next).init({
  resources,
  lng: initial(),
  fallbackLng: 'en',
  defaultNS: 'common',
  ns: Object.keys(resources.en || {}),
  interpolation: { escapeValue: false }, // React escapes already
  returnNull: false,
})
document.documentElement.lang = i18n.language === 'zh' ? 'zh-CN' : 'en'

// tell the server once per load, and on every change
let synced = false
export function syncLang(api) {
  if (synced) return
  synced = true
  api('/api/settings/lang', { method: 'PUT', body: { lang: i18n.language } }).catch(() => {})
}

export async function setLang(lang, api) {
  await i18n.changeLanguage(lang)
  document.documentElement.lang = lang === 'zh' ? 'zh-CN' : 'en'
  try { localStorage.setItem('dot_lang', lang) } catch { /* ignore */ }
  if (api) api('/api/settings/lang', { method: 'PUT', body: { lang } }).catch(() => {})
}

export default i18n
