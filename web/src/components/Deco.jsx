import './Deco.css'
import { usePalette } from '../theme'

// Memphis confetti: flat geometric shapes that float behind a hero area.
// Purely decorative — pointer-events off, aria-hidden, respects reduced motion.
const SHAPES = {
  circle: <circle cx="12" cy="12" r="9" className="d-fill" />,
  ring: <circle cx="12" cy="12" r="8" className="d-stroke" />,
  tri: <path d="M12 3l9.5 17h-19z" className="d-fill" />,
  triline: <path d="M12 4l8.5 15h-17z" className="d-stroke" />,
  plus: <path d="M12 4v16M4 12h16" className="d-stroke" />,
  squig: <path d="M1 14q3.5-8 7 0t7 0 7 0" className="d-stroke" />,
  zig: <path d="M1 16l4-8 4 8 4-8 4 8 4-8" className="d-stroke" />,
  half: <path d="M3 16a9 9 0 0 1 18 0z" className="d-fill" />,
  pill: <rect x="2" y="8" width="20" height="8" rx="4" className="d-fill" />,
  star: <path d="M12 2.5l2.7 6 6.5.6-4.9 4.3 1.5 6.4L12 16.5l-5.8 3.3 1.5-6.4-4.9-4.3 6.5-.6z" className="d-fill" />,
  heart: <path d="M12 20s-7.5-4.6-7.5-10A4.3 4.3 0 0 1 12 7.4 4.3 4.3 0 0 1 19.5 10c0 5.4-7.5 10-7.5 10z" className="d-fill" />,
  wave: <path d="M1 12c2.7-4 5.3-4 8 0s5.3 4 8 0 4.3-3 6-1" className="d-stroke" />,
  leaf: <path d="M4 20C4 10 10 4 20 4c0 10-6 16-16 16zM4 20l9-9" className="d-fill" />,
  square: <rect x="4" y="4" width="16" height="16" rx="2" className="d-fill" />,
  bar: <path d="M3 12h18" className="d-stroke" />,
  dots: (
    <g className="d-dots">
      {[4, 12, 20].flatMap((y) => [4, 12, 20].map((x) => <circle key={`${x}-${y}`} cx={x} cy={y} r="1.8" />))}
    </g>
  ),
}

const PALETTE = ['var(--m-coral)', 'var(--m-yellow)', 'var(--m-teal)', 'var(--m-blue)', 'var(--m-pink)', 'var(--m-lilac)']

// [shape, left%, top%, size px, rotate deg, motion]
const PRESETS = {
  hero: [
    ['circle', 6, 12, 22, 0, 'float'], ['triline', 86, 8, 30, 12, 'spin'], ['squig', 14, 78, 44, -8, 'float'],
    ['dots', 78, 70, 34, 0, 'drift'], ['plus', 92, 44, 20, 0, 'spin'], ['half', 2, 46, 26, 30, 'float'],
    ['ring', 70, 18, 16, 0, 'drift'], ['zig', 30, 4, 36, 0, 'drift'], ['tri', 60, 88, 18, -20, 'float'],
  ],
  card: [
    ['circle', 8, 10, 14, 0, 'float'], ['triline', 82, 6, 18, 15, 'spin'], ['squig', 70, 58, 28, 0, 'drift'],
    ['plus', 12, 60, 12, 0, 'spin'], ['dots', 86, 36, 18, 0, 'float'],
  ],
  rail: [
    ['circle', 18, 30, 10, 0, 'float'], ['triline', 60, 52, 14, 10, 'spin'], ['squig', 10, 70, 26, 0, 'drift'], ['plus', 64, 84, 10, 0, 'spin'],
  ],
}

// each colour scheme redraws the confetti in its own shapes
const SWAP = {
  ocean: { tri: 'circle', triline: 'ring', zig: 'wave', squig: 'wave', half: 'circle', plus: 'ring', pill: 'ring' },
  matcha: { tri: 'leaf', triline: 'plus', zig: 'squig', half: 'leaf', circle: 'leaf', pill: 'square' },
  berry: { tri: 'heart', triline: 'star', plus: 'star', half: 'heart', circle: 'heart', zig: 'squig' },
  noir: { circle: 'square', ring: 'plus', squig: 'bar', zig: 'bar', half: 'square', tri: 'square', triline: 'plus' },
}

export default function Deco({ preset = 'hero', seed = 0, className = '' }) {
  const palette = usePalette()
  const swap = SWAP[palette] || {}
  const items = (PRESETS[preset] || PRESETS.hero).map(([shape, ...rest]) => [swap[shape] || shape, ...rest])
  return (
    <div className={`deco ${className}`} aria-hidden="true">
      {items.map(([shape, x, y, size, rot, motion], i) => (
        <span
          key={i}
          className={`d-shape d-${motion}`}
          style={{
            left: `${x}%`, top: `${y}%`, width: size, height: size,
            '--rot': `${rot}deg`, '--dc': PALETTE[(i + seed) % PALETTE.length],
            animationDelay: `${-((i * 1.7 + seed) % 7)}s`,
          }}
        >
          <svg viewBox="0 0 24 24" width={size} height={size}>{SHAPES[shape]}</svg>
        </span>
      ))}
    </div>
  )
}
