import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, toast, useStore } from '../store'
import Mascot, { animalFor } from '../components/Mascot'
import { go } from '../App'
import { md } from '../util'

const FILES = [
  ['USER.md', 'user'],
  ['MEMORY.md', 'memory'],
  ['SOUL.md', 'soul'],
  ['HEARTBEAT.md', 'heartbeat'],
  ['journal', 'journal'],
]

export default function Memory({ agentId, embedded }) {
  const { t } = useTranslation('memory')
  const { agents } = useStore()
  const aid = agentId || agents[0]?.id
  const agent = agents.find((a) => a.id === aid)
  const [file, setFile] = useState('USER.md')
  const [content, setContent] = useState('')
  const [edit, setEdit] = useState(false)

  useEffect(() => {
    setEdit(false)
    const url = file === 'journal' ? `/api/agents/${aid}/journal` : `/api/agents/${aid}/memory/${file}`
    api(url).then((r) => setContent(r.content || '')).catch(() => setContent(''))
  }, [aid, file])

  const save = async () => {
    await api(`/api/agents/${aid}/memory/${file}`, { method: 'PUT', body: { content } })
    setEdit(false)
    toast(t('saved'))
  }

  return (
    <div>
      <header className="page-head" style={embedded ? { display: 'none' } : null}>
        <div>
          <h1>{t('title')}</h1>
          <p className="muted">{t('intro')}</p>
        </div>
      </header>
      <div className="row gap6 wrap mb12" style={embedded ? { display: 'none' } : null}>
        {agents.map((a) => (
          <button key={a.id} className={`mini-agent ${a.id === aid ? 'on' : ''}`} onClick={() => go('memory', a.id)}>
            <Mascot color={a.color} animal={animalFor(a)} size={22} bubble={false} /> {a.name}
          </button>
        ))}
      </div>
      <div className="mem">
        <div className="mem-files">
          {FILES.map(([f, k]) => (
            <button key={f} className={`mem-file ${file === f ? 'on' : ''}`} onClick={() => setFile(f)}>
              <b>{t(`files.${k}.label`)}</b>
              <small className="muted">{t(`files.${k}.desc`)}</small>
            </button>
          ))}
        </div>
        <div className="mem-doc card">
          <div className="row between mb8">
            <b>{agent?.name} · {file}</b>
            {file !== 'journal' &&
              (edit ? (
                <div className="row gap6">
                  <button className="btn ghost sm" onClick={() => setEdit(false)}>{t('common:cancel')}</button>
                  <button className="btn sm" onClick={save}>{t('common:save')}</button>
                </div>
              ) : (
                <button className="btn ghost sm" onClick={() => setEdit(true)}>{t('edit')}</button>
              ))}
          </div>
          {edit ? (
            <textarea className="input mono mem-edit" value={content} onChange={(e) => setContent(e.target.value)} />
          ) : (
            <div className="md" dangerouslySetInnerHTML={{ __html: md(content || t('empty')) }} />
          )}
        </div>
      </div>
    </div>
  )
}
