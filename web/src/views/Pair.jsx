import { useEffect, useState } from 'react'
import { Trans, useTranslation } from 'react-i18next'
import { api, setToken, store } from '../store'
import Mascot from '../components/Mascot'
import Deco from '../components/Deco'

export default function Pair({ code: initial }) {
  const [code, setCode] = useState(initial || '')
  const [state, setState] = useState('idle')
  const { t } = useTranslation('pair')

  const submit = async (c = code) => {
    setState('busy')
    try {
      const body = c.length > 20 ? { token: c.trim() } : { code: c.trim() }
      const r = await api('/api/pair', { method: 'POST', body })
      setToken(r.token)
      store.set({ authed: true, ready: false })
      location.hash = '#/chats'
    } catch {
      setState('bad')
    }
  }
  useEffect(() => {
    if (initial) submit(initial)
  }, [initial])

  return (
    <div className="pair">
      <Deco />
      <div className="pair-card">
        <Mascot animal="fox" color="#FFB38A" status={state === 'busy' ? 'thinking' : state === 'bad' ? 'error' : 'idle'} size={110} emoji="✨" />
        <h1>{t('hi')}</h1>
        <p className="muted">{t('intro')}</p>
        <input
          className="input big"
          placeholder={t('placeholder')}
          value={code}
          onChange={(e) => setCode(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit()}
          autoFocus
        />
        <button className="btn big" disabled={!code || state === 'busy'} onClick={() => submit()}>
          {state === 'busy' ? t('connecting') : t('connect')}
        </button>
        {state === 'bad' && <p className="err"><Trans t={t} i18nKey="bad" components={{ code: <code /> }} /></p>}
        <p className="muted small"><Trans t={t} i18nKey="hint" components={{ code: <code /> }} /></p>
      </div>
    </div>
  )
}
