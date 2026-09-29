import { useId } from 'react'
import './Mascot.css'
import { SHAPES, EYES, MOUTHS, ANIMAL_KEYS, PAL } from './animals/shapes'

// Agent avatars: flat Memphis animal heads (solid fill, ink outline, hard ground shadow) whose face
// and little props follow the agent's state. Without a known `animal` it falls back to the blob.
// states: idle · thinking · working · waiting · sleeping · error

export const ANIMALS = ANIMAL_KEYS.map((key) => ({ key, label: key[0].toUpperCase() + key.slice(1) }))

// Stable avatar for an agent: its explicit `avatar` if that's a known animal, otherwise a pick from
// a tiny hash both web and iOS agree on — the sum of the UTF-16 code units of (id || name), mod 12
// (see MascotAnimals.animalFor in ios/OpenDot/Office/MascotView.swift).
export function animalFor(agent) {
  if (!agent) return ANIMAL_KEYS[0]
  if (agent.avatar && ANIMAL_KEYS.includes(agent.avatar)) return agent.avatar
  const s = String(agent.id || agent.name || '')
  let h = 0
  for (let i = 0; i < s.length; i++) h += s.charCodeAt(i)
  return ANIMAL_KEYS[h % ANIMAL_KEYS.length]
}

const INK = 'var(--edge, #1e1b2e)'
const tok = (t, main) => (t === 'main' ? main : t === 'ink' ? INK : t === 'white' ? PAL.white : t)

function Shape({ s, main, dw }) {
  const w = s.w ?? dw
  const style = { fill: tok(s.f ?? 'main', main) }
  if (w > 0) Object.assign(style, { stroke: tok(s.s ?? 'ink', main), strokeWidth: w, strokeLinejoin: 'round', strokeLinecap: 'round' })
  if (s.o != null) style.opacity = s.o
  if (s.d) return <path d={s.d} style={style} />
  if (s.c) return <circle cx={s.c[0]} cy={s.c[1]} r={s.c[2]} style={style} />
  const [cx, cy, rx, ry, rot] = s.e
  return <ellipse cx={cx} cy={cy} rx={rx} ry={ry} style={style} transform={rot ? `rotate(${rot} ${cx} ${cy})` : undefined} />
}

const Layer = ({ list, main, dw = 4 }) => (list || []).map((s, i) => <Shape key={i} s={s} main={main} dw={dw} />)

// one shared face part (eye / mouth) drawn around the origin
function FacePart({ parts }) {
  return parts.map((p, i) => {
    const cls = p.k === 'line' ? 'm-line' : p.k === 'glint' ? 'm-glint' : 'm-pupil'
    if (p.d) return <path key={i} d={p.d} className={cls} />
    if (p.c) return <circle key={i} cx={p.c[0]} cy={p.c[1]} r={p.c[2]} className={cls} />
    return <ellipse key={i} cx={p.e[0]} cy={p.e[1]} rx={p.e[2]} ry={p.e[3]} className={cls} />
  })
}

// helper N of a lead: a different animal from the lead, stable per index
export function helperAnimal(agent, i) {
  const mine = agent ? animalFor(agent) : 'fox'
  const faces = ANIMAL_KEYS.filter((k) => k !== mine)
  return faces[i % faces.length]
}

export default function Mascot({ color = '#FFB38A', emoji, status = 'idle', size = 44, bubble = true, animal }) {
  const s = EYES[status] && MOUTHS[status] ? status : 'idle'
  const big = size >= 36
  const kind = SHAPES[animal] && animal !== 'blob' ? animal : 'blob'
  const A = SHAPES[kind]
  const clipId = 'mc' + useId().replace(/[^a-zA-Z0-9_-]/g, '')

  const eyeKey = s === 'idle' && A.idle?.eyes ? A.idle.eyes : s
  const mouthKey = s === 'idle' && A.idle?.mouth ? A.idle.mouth : s
  const es = A.es ?? 1
  const [msx, msy] = Array.isArray(A.ms) ? A.ms : [A.ms ?? 1, A.ms ?? 1]
  const blinks = eyeKey === 'idle'
  const re = A.eyes[1]

  return (
    <div className={`mascot m-${s} m-a-${kind}`} style={{ width: size, height: size, '--mc': color }}>
      <svg viewBox="0 0 100 100" width={size} height={size} aria-hidden="true">
        <ellipse className="m-ground" cx="52" cy="95" rx="26" ry="4" />
        <g className="m-body-wrap">
          <Layer list={A.back} main={color} />
          <path d={A.head} style={{ fill: color }} />
          {A.clip && (
            <>
              <clipPath id={clipId}><path d={A.head} /></clipPath>
              <g clipPath={`url(#${clipId})`}><Layer list={A.clip} main={color} dw={0} /></g>
            </>
          )}
          <path className="m-body" d={A.head} style={{ fill: 'none' }} />
          {A.sh && <path className="m-shine" d={A.sh} />}
          {A.ck && A.ck.map(([x, y], i) => <ellipse key={i} className="m-cheek" cx={x} cy={y} rx="6.5" ry="4.6" fill={PAL.cheek} />)}
          <Layer list={A.front} main={color} />
          <g className={blinks ? 'm-look' : undefined}>
            {A.eyes.map(([x, y], i) => (
              <g key={i} transform={`translate(${x} ${y}) scale(${es})`}>
                <g className={blinks ? 'm-eye' : undefined}><FacePart parts={EYES[eyeKey]} /></g>
              </g>
            ))}
          </g>
          <g transform={`translate(${A.mouth[0]} ${A.mouth[1]}) scale(${msx} ${msy})`}><FacePart parts={MOUTHS[mouthKey]} /></g>
          <Layer list={A.top} main={color} />
          {s === 'working' && big && (
            <path d={`M${re[0] + 19} ${re[1] - 21}q4 6 0 9q-4-3 0-9z`} fill="#9ED8FF" className="m-sweat m-outlined" />
          )}
        </g>
      </svg>
      {emoji && big && kind === 'blob' && <span className="m-badge" style={{ fontSize: Math.max(10, size * 0.26) }}>{emoji}</span>}
      {bubble && s === 'thinking' && (
        <span className="m-orbit" aria-hidden="true"><i /><i /><i /></span>
      )}
      {bubble && s === 'waiting' && <span className="m-bubble q">?</span>}
      {bubble && s === 'sleeping' && <span className="m-zzz"><i>z</i><i>z</i><i>z</i></span>}
      {bubble && s === 'working' && <span className="m-spark"><i>✦</i><i>✧</i></span>}
      {bubble && s === 'error' && <span className="m-bubble bad">!</span>}
    </div>
  )
}
