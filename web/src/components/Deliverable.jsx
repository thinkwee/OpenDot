// A deliverable is a real file (report, spreadsheet, deck, chart, mini-app) an agent
// hands back instead of just talking. Cards render inline in chat (ChatView adds
// `meta.attachments` — see docs/PLAN_V02.md) and in the Library view.
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { api, getToken, toast } from '../store'
import { ago } from '../util'
import './Deliverable.css'
import { cardFile, openFile } from './FileViewer'

// [icon, color, English label] — show the label via t(`files:kind.${kind}`)
export const KIND_META = {
  document: ['📄', '#FFB38A', 'Document'],
  spreadsheet: ['📊', '#8FD6B8', 'Spreadsheet'],
  slides: ['📽️', '#B9A6F2', 'Slides'],
  image: ['🖼️', '#8EC5FF', 'Image'],
  app: ['🌐', '#FFD37A', 'Web page'],
  receipt: ['🧾', '#FF9EC4', 'Receipt'],
  file: ['📁', '#a89cae', 'File'],
}

export function withToken(url) {
  if (!url) return url
  const tok = getToken()
  return url + (url.includes('?') ? '&' : '?') + 'token=' + encodeURIComponent(tok)
}

function nameFrom(res, fallback) {
  const cd = res.headers.get('content-disposition') || ''
  const star = cd.match(/filename\*=utf-8''([^;]+)/i)
  if (star) return decodeURIComponent(star[1])
  const plain = cd.match(/filename="?([^";]+)"?/i)
  return plain ? plain[1] : fallback || 'download'
}

// Phones (especially a home-screen web app on iPhone) ignore <a download>, so fetch
// the file and hand it to the share sheet ("Save to Files", AirDrop, WeChat…);
// desktops get an ordinary download with the real file name.
export async function saveFile(url, fallbackName) {
  try {
    const res = await fetch(withToken(url))
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const blob = await res.blob()
    const name = nameFrom(res, fallbackName)
    const file = new File([blob], name, { type: blob.type || 'application/octet-stream' })
    const touch = matchMedia('(pointer: coarse)').matches
    if (touch && navigator.canShare?.({ files: [file] })) {
      try {
        await navigator.share({ files: [file], title: name })
        return
      } catch (e) {
        if (e.name === 'AbortError') return
      }
    }
    const href = URL.createObjectURL(file)
    const a = document.createElement('a')
    a.href = href
    a.download = name
    document.body.appendChild(a)
    a.click()
    a.remove()
    setTimeout(() => URL.revokeObjectURL(href), 60000)
  } catch (e) {
    toast(e.message)
  }
}

export function fmtSize(n) {
  if (!n) return ''
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

export default function DeliverableCard({ d }) {
  const [open, setOpen] = useState(false)
  const [shared, setShared] = useState(!!d?.share_enabled)
  const [shareUrl, setShareUrl] = useState(null)
  const { t } = useTranslation('files')
  if (!d) return null
  const isReceipt = d.kind === 'receipt'
  const [icon, color] = KIND_META[d.kind] || KIND_META.file
  const label = t(`kind.${KIND_META[d.kind] ? d.kind : 'file'}`)

  const doShare = async (e) => {
    e.stopPropagation()
    try {
      const r = await api(`/api/deliverables/${d.id}/share`, { method: 'POST' })
      setShared(true)
      const full = location.origin + r.url
      setShareUrl(full)
      try {
        await navigator.clipboard.writeText(full)
        toast(t('linkCopied'))
      } catch {
        toast(t('linkReady'))
      }
    } catch (e2) {
      toast(t('shareFailed', { msg: e2.message }))
    }
  }
  const doRevoke = async (e) => {
    e.stopPropagation()
    await api(`/api/deliverables/${d.id}/share`, { method: 'DELETE' })
    setShared(false)
    setShareUrl(null)
    toast(t('revoked'))
  }

  return (
    <>
      <button className={`deliv-card ${isReceipt ? 'receipt' : ''} ${d.status === 'parsing' ? 'parsing' : ''}`} style={{ '--c': color }}
        onClick={() => (isReceipt ? setOpen(true) : openFile(cardFile(d)))} title={d.path || d.title}>
        <span className="deliv-ico" aria-hidden="true">{icon}</span>
        <div className="deliv-info grow">
          <b className="ellipsis">{d.title || t('untitled')}</b>
          <small className="muted ellipsis">
            {isReceipt ? ago(d.created) : d.status === 'parsing' ? t('reading') : d.path || `${label}${d.size ? ` · ${fmtSize(d.size)}` : ''}`}
          </small>
        </div>
        {!isReceipt && <span className="deliv-chev">›</span>}
      </button>
      {open && (
        <div className="modal-bg" onClick={() => setOpen(false)}>
          <div className="modal deliv-modal" onClick={(e) => e.stopPropagation()}>
            <div className="row between mb8">
              <h2 className="ellipsis">{d.title}</h2>
              <button className="icon-btn" onClick={() => setOpen(false)} aria-label={t('common:close')}>×</button>
            </div>
            <DeliverablePreview d={d} />
            {!isReceipt && (
              <div className="row gap6 wrap mt8">
                <a className="btn ghost sm" href={withToken(d.preview_url)} target="_blank" rel="noopener noreferrer">
                  {t('open')}
                </a>
                {d.url && (
                  <button className="btn ghost sm" onClick={() => saveFile(d.url, d.title)}>{t('download')}</button>
                )}
                {!shared ? (
                  <button className="btn ghost sm" onClick={doShare}>{t('share')}</button>
                ) : (
                  <button className="btn danger sm" onClick={doRevoke}>{t('revoke')}</button>
                )}
              </div>
            )}
            {shareUrl && (
              <div className="small muted mt8 row gap6">
                <span>{t('publicLink')}</span>
                <code className="ellipsis grow">{shareUrl}</code>
              </div>
            )}
          </div>
        </div>
      )}
    </>
  )
}

function DeliverablePreview({ d }) {
  if (d.kind === 'receipt') return <ReceiptDetail d={d} />
  if (d.kind === 'image') return <img className="deliv-img" src={withToken(d.url || d.preview_url)} alt={d.title} />
  return <iframe className="deliv-frame" src={withToken(d.preview_url)} title={d.title} />
}

function ReceiptDetail({ d }) {
  const { t } = useTranslation('files')
  const meta = d.meta || {}
  return (
    <div className="receipt-detail">
      <div className="row gap8 mb8">
        <span className="deliv-ico big">🧾</span>
        <div>
          <b>{d.title}</b>
          <div className="muted small">{ago(d.created)}</div>
        </div>
      </div>
      {meta.args && Object.keys(meta.args).length > 0 && (
        <pre className="code">{JSON.stringify(meta.args, null, 2)}</pre>
      )}
      {meta.result && (
        <details>
          <summary className="muted small">{t('result')}</summary>
          <pre className="code">{JSON.stringify(meta.result, null, 2)}</pre>
        </details>
      )}
    </div>
  )
}
