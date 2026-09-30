import {
  siAirtable, siAsana, siAtlassian, siBrave, siClickup, siDropbox, siEvernote, siFigma, siGit, siGithub,
  siGmail, siGooglecalendar, siIcloud, siGoogledrive, siGooglemaps, siHomeassistant, siHuggingface, siIntercom,
  siLinear, siMiro, siNetlify, siNotion, siObsidian, siSentry, siSupabase, siTodoist, siVercel,
  siWebflow, siWix, siZapier,
} from 'simple-icons'

// brand marks for the app directory (Simple Icons, CC0); anything else gets a letter tile
const ICONS = Object.fromEntries([
  siAirtable, siAsana, siAtlassian, siBrave, siClickup, siDropbox, siEvernote, siFigma, siGit, siGithub,
  siGmail, siGooglecalendar, siIcloud, siGoogledrive, siGooglemaps, siHomeassistant, siHuggingface, siIntercom,
  siLinear, siMiro, siNetlify, siNotion, siObsidian, siSentry, siSupabase, siTodoist, siVercel,
  siWebflow, siWix, siZapier,
].map((i) => [i.slug, i]))

function light(hex) {
  const h = (hex || '#888').replace('#', '')
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255)
  return 0.2126 * r + 0.7152 * g + 0.0722 * b > 0.62
}

export default function AppIcon({ app, size = 40 }) {
  const color = app?.color || '#8b8698'
  const ink = light(color) ? '#1e1b2e' : '#fff'
  const icon = ICONS[app?.icon]
  const name = typeof app?.name === 'object' ? app.name.en : app?.label || app?.name || '?'
  return (
    <span className="app-icon" style={{ width: size, height: size, background: color, color: ink, borderRadius: size * 0.28 }}>
      {icon ? (
        <svg viewBox="0 0 24 24" width={size * 0.56} height={size * 0.56} fill="currentColor" aria-hidden="true"><path d={icon.path} /></svg>
      ) : (
        <b style={{ fontSize: size * 0.44 }}>{name.replace(/[^\p{L}\p{N}]/gu, '').slice(0, 1).toUpperCase()}</b>
      )}
    </span>
  )
}
