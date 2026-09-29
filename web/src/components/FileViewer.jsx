// Universal file viewer — open any file from anywhere (chat cards, paths in agent
// messages, the Files list, uploads). Each format is rendered by a mature OSS
// library, lazy-loaded so the main bundle stays small:
//   PDF → the browser's viewer · Word → docx-preview · Excel/CSV/ODS → SheetJS ·
//   PowerPoint → pptx-preview · code/JSON → highlight.js · Markdown → marked ·
//   HTML → sandboxed iframe · images/audio/video → native.
// Anything else (EPUB, .msg, notebooks, archives, legacy .doc/.ppt …) falls back to
// the server's MarkItDown/OCR text extraction.
import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import DOMPurify from 'dompurify'
import {
  Download, ExternalLink, File, FileArchive, FileCode, FileText, Globe, Image as ImageIcon,
  Link2, Music, Presentation, Sheet, Type, Video, X,
} from 'lucide-react'
import { api, getToken, toast } from '../store'
import { md } from '../util'
import { fmtSize, saveFile, withToken } from './Deliverable'
import './FileViewer.css'

const EXT = (name = '') => (name.split('.').pop() || '').toLowerCase()
const IMG = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg', 'avif', 'ico']
const AUDIO = ['mp3', 'wav', 'm4a', 'aac', 'flac', 'ogg', 'opus']
const VIDEO = ['mp4', 'mov', 'webm', 'mkv', 'm4v']
const SHEET = ['xlsx', 'xls', 'xlsm', 'xlsb', 'ods', 'csv', 'tsv', 'numbers']
const CODE_LANG = {
  py: 'python', js: 'javascript', jsx: 'javascript', mjs: 'javascript', cjs: 'javascript', ts: 'typescript',
  tsx: 'typescript', json: 'json', jsonl: 'json', java: 'java', kt: 'kotlin', swift: 'swift', go: 'go',
  rs: 'rust', c: 'c', h: 'c', cc: 'cpp', cpp: 'cpp', hpp: 'cpp', cs: 'csharp', rb: 'ruby', php: 'php',
  sh: 'bash', bash: 'bash', zsh: 'bash', sql: 'sql', r: 'r', lua: 'lua', yaml: 'yaml', yml: 'yaml',
  toml: 'ini', ini: 'ini', cfg: 'ini', xml: 'xml', css: 'css', scss: 'scss', less: 'less', diff: 'diff',
  patch: 'diff', dockerfile: 'dockerfile', makefile: 'makefile', tex: 'latex', pl: 'perl', m: 'objectivec',
  scala: 'scala', dart: 'dart', vue: 'xml', svelte: 'xml', graphql: 'graphql', proto: 'protobuf', txt: 'plaintext',
  log: 'plaintext', env: 'bash', srt: 'plaintext', vtt: 'plaintext', rst: 'plaintext',
}

export function kindOf(name) {
  const e = EXT(name)
  if (IMG.includes(e)) return 'image'
  if (e === 'pdf') return 'pdf'
  if (AUDIO.includes(e)) return 'audio'
  if (VIDEO.includes(e)) return 'video'
  if (e === 'docx') return 'docx'
  if (SHEET.includes(e)) return 'sheet'
  if (e === 'pptx') return 'pptx'
  if (e === 'md' || e === 'markdown') return 'markdown'
  if (e === 'html' || e === 'htm') return 'html'
  if (e in CODE_LANG) return 'code'
  return 'other'
}

const ICON = {
  image: [ImageIcon, 'var(--m-blue)'], pdf: [FileText, 'var(--m-coral)'], audio: [Music, 'var(--m-pink)'],
  video: [Video, 'var(--m-pink)'], docx: [FileText, 'var(--m-blue)'], sheet: [Sheet, 'var(--m-teal)'],
  pptx: [Presentation, 'var(--m-coral)'], markdown: [FileText, 'var(--m-yellow)'], html: [Globe, 'var(--m-yellow)'],
  code: [FileCode, 'var(--m-lilac)'], other: [File, 'var(--m-lilac)'], archive: [FileArchive, 'var(--m-lilac)'],
}
export function FileIcon({ name, size = 20 }) {
  const k = ['zip', 'tar', 'gz', 'tgz', '7z', 'rar'].includes(EXT(name)) ? 'archive' : kindOf(name)
  const [Ico, c] = ICON[k] || ICON.other
  return <span className="fv-ico" style={{ '--fc': c }}><Ico size={size} strokeWidth={2.2} /></span>
}

// ---- building a "file ref" from the places files come from ----
const q = encodeURIComponent
export function agentFile(agentId, path) {
  const base = `/api/view/raw?agent=${q(agentId)}&path=${q(path)}`
  return { name: path.split('/').pop(), path, raw: base, text: `/api/view/text?agent=${q(agentId)}&path=${q(path)}`, download: base + '&download=1' }
}
export function cardFile(d) {
  if (d.workfile) return { ...agentFile(d.agent_id, d.path), size: d.size }
  if (d.upload) return { name: d.title, path: d.path, size: d.size, raw: `/api/uploads/${d.id}/file`, text: `/api/uploads/${d.id}/text`, download: `/api/uploads/${d.id}/file` }
  return {
    name: d.title && EXT(d.title) ? d.title : (d.path || d.url || '').split('/').pop() || d.title,
    title: d.title, size: d.size, raw: `/api/deliverables/${d.id}/file`, text: `/api/deliverables/${d.id}/text`,
    download: `/api/deliverables/${d.id}/file`, preview: d.preview_url, deliverableId: d.id, kindHint: d.kind,
  }
}
export function openFile(ref) {
  window.dispatchEvent(new CustomEvent('dot:open-file', { detail: ref }))
}

async function fetchBlob(url) {
  const r = await fetch(url, { headers: { Authorization: `Bearer ${getToken()}` } })
  if (!r.ok) throw new Error((await r.text()) || r.statusText)
  return r.blob()
}

// ---- host: mount once, opens whenever openFile() is called ----
export function FileViewerHost() {
  const [ref, setRef] = useState(null)
  useEffect(() => {
    const on = (e) => setRef(e.detail)
    window.addEventListener('dot:open-file', on)
    return () => window.removeEventListener('dot:open-file', on)
  }, [])
  useEffect(() => {
    if (!ref) return
    const esc = (e) => e.key === 'Escape' && setRef(null)
    window.addEventListener('keydown', esc)
    return () => window.removeEventListener('keydown', esc)
  }, [ref])
  if (!ref) return null
  return createPortal(<Viewer key={ref.raw} f={ref} onClose={() => setRef(null)} />, document.body)
}

function Viewer({ f, onClose }) {
  // a deliverable's name may lack an extension (title) — fall back to its kind
  let kind = kindOf(f.name)
  if (kind === 'other' && f.kindHint === 'app') kind = 'html'
  const [asText, setAsText] = useState(kind === 'other')
  const { t } = useTranslation('files')
  const share = async () => {
    try {
      const r = await api(`/api/deliverables/${f.deliverableId}/share`, { method: 'POST' })
      const full = location.origin + r.url
      try { await navigator.clipboard.writeText(full); toast(t('linkCopied')) } catch { toast('🔗 ' + full) }
    } catch (e) { toast(t('shareFailed', { msg: e.message })) }
  }
  return (
    <div className="modal-bg fv-bg" onClick={onClose}>
      <div className="fv" onClick={(e) => e.stopPropagation()} role="dialog" aria-label={f.name}>
        <header className="fv-head">
          <FileIcon name={f.name} size={22} />
          <div className="grow fv-title">
            <b className="ellipsis">{f.title || f.name}</b>
            <small className="muted ellipsis">{[EXT(f.name).toUpperCase(), fmtSize(f.size), f.path].filter(Boolean).join(' · ')}</small>
          </div>
          <div className="fv-tools">
            {kind !== 'other' && (
              <button className={`btn ghost sm ${asText ? 'on' : ''}`} onClick={() => setAsText((v) => !v)} title={t('viewer.textTitle')}>
                <Type size={15} /> <span className="hide-sm">{t('viewer.text')}</span>
              </button>
            )}
            <a className="btn ghost sm" href={withToken(f.raw)} target="_blank" rel="noopener noreferrer" title={t('viewer.newTab')}><ExternalLink size={15} /></a>
            <button className="btn ghost sm" onClick={() => saveFile(f.download || f.raw, f.name)} title={t('viewer.download')}><Download size={15} /></button>
            {f.deliverableId && <button className="btn ghost sm" onClick={share} title={t('viewer.shareTitle')}><Link2 size={15} /></button>}
            <button className="icon-btn" onClick={onClose} aria-label={t('common:close')}><X size={20} /></button>
          </div>
        </header>
        <div className="fv-body">
          {asText ? <TextView f={f} /> : <Render f={f} kind={kind} onFail={() => setAsText(true)} />}
        </div>
      </div>
    </div>
  )
}

function Loading() {
  const { t } = useTranslation('files')
  return <div className="fv-empty"><span className="fv-spin" /> {t('viewer.opening')}</div>
}

function Render({ f, kind, onFail }) {
  if (kind === 'image') return <ImageView f={f} />
  if (kind === 'pdf') return <PdfView f={f} onFail={onFail} />
  if (kind === 'audio') return <div className="fv-media"><audio controls src={withToken(f.raw)} /></div>
  if (kind === 'video') return <div className="fv-media"><video controls src={withToken(f.raw)} /></div>
  if (kind === 'docx') return <DocxView f={f} onFail={onFail} />
  if (kind === 'sheet') return <SheetView f={f} onFail={onFail} />
  if (kind === 'pptx') return <PptxView f={f} onFail={onFail} />
  if (kind === 'html') return <HtmlView f={f} onFail={onFail} />
  if (kind === 'markdown') return <MarkdownView f={f} onFail={onFail} />
  if (kind === 'code') return <CodeView f={f} onFail={onFail} />
  return <TextView f={f} />
}

function ImageView({ f }) {
  const [zoom, setZoom] = useState(false)
  return (
    <div className={`fv-img ${zoom ? 'zoom' : ''}`} onClick={() => setZoom((z) => !z)}>
      <img src={withToken(f.raw)} alt={f.name} />
    </div>
  )
}

function useBlob(url, onFail) {
  const [blob, setBlob] = useState(null)
  useEffect(() => {
    let alive = true
    fetchBlob(url).then((b) => alive && setBlob(b)).catch(() => alive && onFail?.())
    return () => { alive = false }
  }, [url])
  return blob
}

// pdf.js (Mozilla) — same rendering on desktop, iOS and Android (no plugin needed)
function PdfView({ f, onFail }) {
  const el = useRef(null)
  const blob = useBlob(f.raw, onFail)
  const [pages, setPages] = useState(0)
  useEffect(() => {
    if (!blob || !el.current) return
    let alive = true
    let doc
    ;(async () => {
      const [pdfjs, worker] = await Promise.all([import('pdfjs-dist'), import('pdfjs-dist/build/pdf.worker.min.mjs?url')])
      pdfjs.GlobalWorkerOptions.workerSrc = worker.default
      doc = await pdfjs.getDocument({ data: await blob.arrayBuffer() }).promise
      if (!alive) return
      setPages(doc.numPages)
      const width = Math.min(el.current.clientWidth - 32, 980)
      const dpr = Math.min(window.devicePixelRatio || 1, 2)
      for (let i = 1; i <= Math.min(doc.numPages, 200) && alive; i++) {
        const page = await doc.getPage(i)
        const base = page.getViewport({ scale: 1 })
        const vp = page.getViewport({ scale: (width / base.width) * dpr })
        const canvas = document.createElement('canvas')
        canvas.width = vp.width
        canvas.height = vp.height
        canvas.style.width = `${vp.width / dpr}px`
        canvas.className = 'fv-pdf-page'
        el.current?.appendChild(canvas)
        await page.render({ canvasContext: canvas.getContext('2d'), viewport: vp }).promise
      }
    })().catch(() => alive && onFail())
    return () => { alive = false; doc?.destroy?.() }
  }, [blob])
  return <div className="fv-pdf" ref={el}>{!pages && <Loading />}</div>
}

function DocxView({ f, onFail }) {
  const el = useRef(null)
  const blob = useBlob(f.raw, onFail)
  useEffect(() => {
    if (!blob || !el.current) return
    import('docx-preview')
      .then(({ renderAsync }) => renderAsync(blob, el.current, null, { inWrapper: true, ignoreLastRenderedPageBreak: true, breakPages: true }))
      .catch(onFail)
  }, [blob])
  return <div className="fv-docx" ref={el}>{!blob && <Loading />}</div>
}

function SheetView({ f, onFail }) {
  const [book, setBook] = useState(null)
  const [sheet, setSheet] = useState(0)
  const blob = useBlob(f.raw, onFail)
  useEffect(() => {
    if (!blob) return
    Promise.all([import('xlsx'), blob.arrayBuffer()])
      .then(([XLSX, buf]) => {
        const wb = XLSX.read(buf, { type: 'array', dense: true, sheetRows: 3000 })
        setBook({ XLSX, wb })
      })
      .catch(onFail)
  }, [blob])
  if (!book) return <Loading />
  const { XLSX, wb } = book
  const name = wb.SheetNames[sheet]
  const html = DOMPurify.sanitize(XLSX.utils.sheet_to_html(wb.Sheets[name], { header: '', footer: '' }))
  return (
    <div className="fv-sheet">
      {wb.SheetNames.length > 1 && (
        <div className="fv-sheet-tabs">
          {wb.SheetNames.map((n, i) => <button key={n} className={i === sheet ? 'on' : ''} onClick={() => setSheet(i)}>{n}</button>)}
        </div>
      )}
      <div className="fv-table" dangerouslySetInnerHTML={{ __html: html }} />
    </div>
  )
}

function PptxView({ f, onFail }) {
  const el = useRef(null)
  const blob = useBlob(f.raw, onFail)
  useEffect(() => {
    if (!blob || !el.current) return
    let alive = true
    Promise.all([import('pptx-preview'), blob.arrayBuffer()])
      .then(([mod, buf]) => {
        if (!alive) return
        const w = Math.min(el.current.clientWidth - 24, 960)
        const viewer = mod.init(el.current, { width: w, height: Math.round((w * 9) / 16), mode: 'list' })
        return viewer.preview(buf)
      })
      .catch(onFail)
    return () => { alive = false }
  }, [blob])
  return <div className="fv-pptx" ref={el}>{!blob && <Loading />}</div>
}

function useText(url, onFail) {
  const [text, setText] = useState(null)
  const { t } = useTranslation('files')
  useEffect(() => {
    let alive = true
    fetchBlob(url).then((b) => b.text()).then((t) => alive && setText(t)).catch((e) => {
      if (!alive) return
      if (onFail) onFail()
      else setText(t('viewer.couldNotOpen', { msg: e.message }))
    })
    return () => { alive = false }
  }, [url])
  return text
}

function HtmlView({ f, onFail }) {
  const text = useText(f.raw, onFail)
  if (text == null) return <Loading />
  // no allow-same-origin: the page can run its own scripts but can't touch OpenDot
  return <iframe className="fv-frame white" sandbox="allow-scripts allow-popups allow-forms allow-modals" srcDoc={text} title={f.name} />
}

function MarkdownView({ f, onFail }) {
  const text = useText(f.raw, onFail)
  if (text == null) return <Loading />
  return <article className="fv-doc md" dangerouslySetInnerHTML={{ __html: md(text) }} />
}

function CodeView({ f, onFail }) {
  const text = useText(f.raw, onFail)
  const [html, setHtml] = useState(null)
  useEffect(() => {
    if (text == null) return
    let src = text
    const e = EXT(f.name)
    if (e === 'json') { try { src = JSON.stringify(JSON.parse(text), null, 2) } catch { /* keep as is */ } }
    if (src.length > 400_000) { setHtml(null); return }
    import('highlight.js/lib/common').then(({ default: hljs }) => {
      const lang = CODE_LANG[e]
      const out = lang && hljs.getLanguage(lang) ? hljs.highlight(src, { language: lang, ignoreIllegals: true }) : hljs.highlightAuto(src)
      setHtml(out.value)
    }).catch(() => setHtml(null))
  }, [text])
  if (text == null) return <Loading />
  const lines = (text.match(/\n/g) || []).length + 1
  return (
    <div className="fv-code">
      <div className="fv-gutter" aria-hidden="true">{Array.from({ length: Math.min(lines, 20000) }, (_, i) => <span key={i}>{i + 1}</span>)}</div>
      {html != null ? <pre className="hljs" dangerouslySetInnerHTML={{ __html: html }} /> : <pre>{text}</pre>}
    </div>
  )
}

function TextView({ f }) {
  const text = useText(f.text)
  const { t } = useTranslation('files')
  if (text == null) return <div className="fv-empty"><span className="fv-spin" /> {t('viewer.readingFile')}</div>
  return <article className="fv-doc md" dangerouslySetInnerHTML={{ __html: md(text) }} />
}
