// "What is OpenDot?" — the guide page: what it's for, in plain words.
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Brain, CalendarDays, ChevronRight, KeyRound, ListChecks, Monitor, QrCode, ShieldCheck,
  Sparkles, Star,
} from 'lucide-react'
import { siGithub } from 'simple-icons'
import Mascot from '../components/Mascot'
import Deco from '../components/Deco'
import { go } from '../App'
import { api } from '../store'
import { setLang } from '../i18n'
import './About.css'

export const REPO = 'thinkwee/OpenDot'
const REPO_URL = `https://github.com/${REPO}`

export function Github({ size = 18 }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d={siGithub.path} /></svg>
}

// icons for the pillars, paired with locales/*/about.json → pillars by index
const PILLAR_ICONS = [Sparkles, Monitor, KeyRound, QrCode, CalendarDays, ListChecks, ShieldCheck, Brain]

const CREW = [['#FFB38A', 'fox'], ['#8FD6B8', 'bear'], ['#B9A6F2', 'bunny'], ['#8EC5FF', 'penguin']]

function useStars() {
  const [n, setN] = useState(null)
  useEffect(() => {
    fetch(`https://api.github.com/repos/${REPO}`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => d && typeof d.stargazers_count === 'number' && setN(d.stargazers_count))
      .catch(() => {})
  }, [])
  return n
}

export default function About() {
  const { i18n } = useTranslation('about') // re-renders on language change
  const lang = i18n.language === 'zh' ? 'zh' : 'en'
  // the copy is mostly arrays (day, pillars, steps…), so read the whole bundle
  const t = i18n.getResourceBundle(lang, 'about')
  const stars = useStars()
  const switchLang = (l) => setLang(l, api)

  return (
    <div className={`about lang-${lang}`}>
      <div className="about-lang seg">
        <button className={lang === 'en' ? 'on' : ''} onClick={() => switchLang('en')}>EN</button>
        <button className={lang === 'zh' ? 'on' : ''} onClick={() => switchLang('zh')}>中文</button>
      </div>

      {/* hero */}
      <section className="about-hero">
        <Deco seed={1} />
        <div className="hero-copy">
          <span className="kicker"><Sparkles size={14} /> {t.kicker}</span>
          <h1 className="hero-title">{t.title[0]}<mark>{t.title[1]}</mark>{t.title[2]}</h1>
          <p className="hero-lead">{t.lead}</p>
          <div className="row gap8 wrap">
            <a className="btn big-ish gh" href={REPO_URL} target="_blank" rel="noopener noreferrer">
              <Github size={18} /> {t.star} {stars != null && <span className="gh-count"><Star size={13} fill="currentColor" /> {stars}</span>}
            </a>
            <button className="btn ghost big-ish" onClick={() => go('chats')}>{t.try} <ChevronRight size={16} /></button>
            <button className="btn ghost big-ish" onClick={() => go('examples')}>💡 {t.examples}</button>
          </div>
        </div>
        <div className="hero-crew" aria-hidden="true">
          {CREW.map(([c, e], i) => (
            <div key={c} className={`crew c${i}`}><Mascot color={c} animal={e} size={i === 0 ? 120 : 84} status={['idle', 'working', 'thinking', 'idle'][i]} /></div>
          ))}
          <div className="crew-table" />
        </div>
      </section>

      {/* a day */}
      <section className="about-sec">
        <h2 className="sec-title">{t.dayTitle}</h2>
        <div className="day">
          {t.day.map(([time, h, body], i) => (
            <div key={time} className={`day-item d${i}`}>
              <span className="day-time">{time}</span>
              <div className="day-card">
                <h3>{h}</h3>
                <p>{body}</p>
              </div>
            </div>
          ))}
        </div>
      </section>

      {/* pillars */}
      <section className="about-sec">
        <h2 className="sec-title">{t.pillarsTitle}</h2>
        <div className="pillars">
          {t.pillars.map(([h, body], i) => { const Ico = PILLAR_ICONS[i]; return (
            <div key={h} className="pillar" style={{ '--pc': ['var(--m-coral)', 'var(--m-teal)', 'var(--m-yellow)', 'var(--m-blue)', 'var(--m-pink)', 'var(--m-lilac)'][i % 6] }}>
              <span className="pillar-ico"><Ico size={22} strokeWidth={2.3} /></span>
              <h3>{h}</h3>
              <p>{body}</p>
            </div>
          ) })}
        </div>
      </section>

      {/* ownership */}
      <section className="about-sec own">
        <h2 className="sec-title">{t.ownTitle}</h2>
        <div className="own-strip">
          {t.own.map(([h, body], i) => (
            <div key={h} className={`own-item o${i}`}><b>{h}</b><p>{body}</p></div>
          ))}
        </div>
      </section>

      {/* start + status */}
      <section className="about-sec two">
        <div className="card start-card">
          <h3>{t.startTitle}</h3>
          <ol className="steps3">
            {t.steps.map(([h, b], i) => <li key={h}><span className="step-n">{i + 1}</span><div><b>{h}</b><p>{b}</p></div></li>)}
          </ol>
        </div>
        <div className="card status-card">
          <h3>{t.statusTitle}</h3>
          <p>{t.status}</p>
          <a className="btn ghost sm mt8" href={`${REPO_URL}#readme`} target="_blank" rel="noopener noreferrer"><Github size={14} /> README</a>
        </div>
      </section>

      <StarNudge t={t} />
    </div>
  )
}

// the cute "give us a star" corner — dismissible, remembers the answer
function StarNudge({ t }) {
  const [state, setState] = useState(() => {
    try { return localStorage.getItem('dot_star_nudge') || 'show' } catch { return 'show' }
  })
  const save = (v) => { setState(v); try { localStorage.setItem('dot_star_nudge', v) } catch { /* ignore */ } }
  if (state === 'later') return null
  if (state === 'starred')
    return (
      <div className="star-nudge thanks">
        <div className="nudge-crew">{CREW.slice(0, 3).map(([c, e]) => <Mascot key={c} color={c} animal={e} size={40} status="working" bubble={false} />)}</div>
        <b>{t.thanks}</b>
      </div>
    )
  return (
    <aside className="star-nudge">
      <div className="nudge-mascot">
        <Mascot color="#FFB38A" animal="fox" size={64} status="waiting" bubble={false} />
        <span className="nudge-star"><Star size={26} fill="currentColor" strokeWidth={2} /></span>
      </div>
      <div className="grow">
        <b>{t.nudgeTitle}</b>
        <p>{t.nudge}</p>
        <div className="row gap6 wrap">
          <a className="btn sm" href={REPO_URL} target="_blank" rel="noopener noreferrer" onClick={() => setTimeout(() => save('starred'), 400)}>
            <Star size={14} fill="currentColor" /> {t.nudgeBtn}
          </a>
          <button className="btn ghost sm" onClick={() => save('later')}>{t.nudgeLater}</button>
        </div>
      </div>
    </aside>
  )
}
