// Hand-drawn Memphis animal heads for <Mascot animal="…" />.
//
// Everything lives in the same 100×100 viewBox as the original blob. Each animal is pure data so the
// iOS port (ios/OpenDot/Office/MascotView.swift) can mirror it 1:1 — if you change a shape here,
// copy the same numbers there.
//
// Shape:  { d: 'svg path' } | { c: [cx, cy, r] } | { e: [cx, cy, rx, ry, rotDeg?] }
//   f: fill token  — 'main' (the agent's color) · 'ink' · 'none' · '#rrggbb'   (default 'main')
//   s: stroke token — same tokens                                              (default 'ink')
//   w: stroke width — default 4 (0 = no stroke; `clip` layers default to 0)
//   o: opacity (default 1)
// Paths only use M L H V C Q Z (upper/lower case) so the Swift parser stays tiny.
//
// Layers, drawn in order:
//   back  → behind the head (ears, gills, tufts)
//   head  → the silhouette (filled 'main', ink outline); also the clip for `clip`
//   clip  → markings clipped inside the head (masks, bibs, bandanas), drawn under the head outline
//   front → on top of the head but under the face (muzzles, beaks, eye patches, lens tint)
//   (face) → shared status face: eyes at `eyes`, mouth at `mouth`
//   top   → accessories over everything (beret, fringe, rims, bow tie, earring)
//
// Face placement: eyes: [[lx, ly], [rx, ry]], es: eye scale, mouth: [x, y], ms: mouth scale,
// ck: cheek centers (or null), idle: optional idle face variant { eyes: 'side', mouth: 'smirk' }.

export const PAL = {
  ink: '#1e1b2e', coral: '#ff6b57', yellow: '#ffd23f', teal: '#2ec4b6', blue: '#4d7cfe',
  pink: '#ff8fc7', lilac: '#a78bfa', white: '#ffffff', cheek: '#FF7E9D',
}
const { coral, yellow, teal, pink, white } = PAL

export const SHAPES = {
  // the original round buddy — the fallback when no animal is given
  blob: {
    head: 'M50 8C72 8 90 24 90 48C90 72 74 92 50 92C26 92 10 72 10 48C10 24 28 8 50 8Z',
    sh: 'M24 34Q28 20 42 15',
    eyes: [[37, 47], [63, 47]], mouth: [50, 60], ck: [[26, 58], [74, 58]],
  },

  // sharp asymmetric yellow fringe, ink-tipped ears, white cheek ruff
  fox: {
    back: [
      { d: 'M14 52L10 6L46 30Z' },
      { d: 'M56 28L90 8L88 54Z' },
      { d: 'M17 36L15 16L32 28Z', f: 'ink', w: 0 },
      { d: 'M70 26L84 17L83 36Z', f: 'ink', w: 0 },
    ],
    head: 'M50 24C68 24 84 30 90 44C94 54 88 64 78 72L58 88Q50 94 42 88L22 72C12 64 6 54 10 44C16 30 32 24 50 24Z',
    clip: [{ d: 'M0 52C18 54 38 58 50 72C62 58 82 54 100 52V100H0Z', f: white }],
    front: [{ d: 'M45 70L55 70L50 76Z', f: 'ink', w: 2.5 }],
    top: [
      { d: 'M22 40C26 24 48 18 68 24C78 27 84 34 86 44L74 36L75 46L63 36L59 44L52 35C42 32 30 34 22 40Z', f: yellow, w: 3.2 },
    ],
    eyes: [[35, 53], [65, 53]], es: 0.9, mouth: [50, 81], ms: 0.75, ck: [[22, 62], [78, 62]],
  },

  // tiny tilted ink beret, tabby forehead stripes, long whiskers
  cat: {
    head: 'M18 42L14 10L40 26Q50 23 60 26L86 10L82 42C90 54 90 72 80 82C70 92 30 92 20 82C10 72 10 54 18 42Z',
    clip: [
      { d: 'M20 32L18 18L32 26Z', f: pink, w: 0 },
      { d: 'M42 32L45 40M50 31V40M58 32L55 40', f: 'none', w: 3.5 },
    ],
    front: [{ d: 'M45 61L55 61L50 66Z', f: pink, w: 2.5 }],
    top: [
      { d: 'M14 62L2 58M14 68L3 71M86 62L98 58M86 68L97 71', f: 'none', w: 3 },
      { d: 'M40 23C42 10 70 2 88 12C93 16 91 23 83 25C68 28 52 29 40 23Z', f: 'ink', w: 3.5 },
      { d: 'M65 7L67 1', f: 'none', w: 4 },
      { d: 'M52 14Q60 9 72 9', f: 'none', s: white, w: 2.6, o: 0.8 },
    ],
    eyes: [[36, 52], [64, 52]], mouth: [50, 69], ms: 0.85, ck: [[24, 64], [76, 64]],
  },

  // one ear up with a yellow clip, one ear flopped over
  bunny: {
    back: [
      { d: 'M28 36C20 22 20 2 31 0C42 -2 45 18 42 34Z' },
      { d: 'M31 30C27 20 27 8 32 6C37 5 38 20 37 30Z', f: pink, w: 0 },
      { d: 'M54 34C51 18 60 4 73 4C86 4 97 14 98 30C99 40 90 42 89 34C88 24 82 17 75 19C69 21 66 28 66 34Z' },
      { d: 'M71 10C82 9 92 17 93 28C89 21 83 15 76 15C73 15 71 13 71 10Z', f: pink, w: 0 },
    ],
    head: 'M50 26C72 26 86 42 86 60C86 80 70 90 50 90C30 90 14 80 14 60C14 42 28 26 50 26Z',
    front: [{ d: 'M46 62L54 62L50 66Z', f: pink, w: 2.5 }],
    top: [{ d: 'M22 17L40 12L41.5 17.5L23.5 22.5Z', f: yellow, w: 2.8 }],
    eyes: [[37, 53], [63, 53]], mouth: [50, 71], ms: 0.85, ck: [[26, 66], [74, 66]],
  },

  // round rose-tinted shades, white muzzle
  bear: {
    back: [
      { c: [24, 28, 12] },
      { c: [76, 28, 12] },
      { c: [24, 28, 5.5], f: white, w: 0, o: 0.55 },
      { c: [76, 28, 5.5], f: white, w: 0, o: 0.55 },
    ],
    head: 'M50 20C74 20 90 34 90 56C90 78 74 90 50 90C26 90 10 78 10 56C10 34 26 20 50 20Z',
    front: [
      { e: [50, 72, 15, 11], f: white, w: 3 },
      { d: 'M44 64Q50 61 56 64Q55 69 50 69Q45 69 44 64Z', f: 'ink', w: 0 },
      { c: [34, 49, 11.5], f: pink, w: 0, o: 0.75 },
      { c: [66, 49, 11.5], f: pink, w: 0, o: 0.75 },
    ],
    top: [
      { c: [34, 49, 11.5], f: 'none', w: 3.6 },
      { c: [66, 49, 11.5], f: 'none', w: 3.6 },
      { d: 'M45.5 47Q50 44 54.5 47M22.5 47L11 43M77.5 47L89 43', f: 'none', w: 3.6 },
      { d: 'M39 57Q42 55 43 52', f: 'none', s: white, w: 2.4 },
      { d: 'M71 57Q74 55 75 52', f: 'none', s: white, w: 2.4 },
    ],
    eyes: [[34, 49], [66, 49]], es: 0.8, mouth: [50, 76], ms: 0.7, ck: [[20, 66], [80, 66]],
  },

  // bulging eye domes, wide grin, one gold hoop earring
  frog: {
    head: 'M50 38C76 38 94 50 94 66C94 82 74 90 50 90C26 90 6 82 6 66C6 50 24 38 50 38Z',
    front: [
      { c: [29, 38, 15] },
      { c: [71, 38, 15] },
      { c: [29, 39, 9.5], f: white, w: 3 },
      { c: [71, 39, 9.5], f: white, w: 3 },
      { c: [46, 58, 1.8], f: 'ink', w: 0 },
      { c: [54, 58, 1.8], f: 'ink', w: 0 },
    ],
    top: [
      { d: 'M7 72V78', f: 'none', w: 3 },
      { c: [7, 84, 6.5], f: 'none', w: 7 },
      { c: [7, 84, 6.5], f: 'none', s: yellow, w: 3.4 },
    ],
    eyes: [[29, 39], [71, 39]], es: 0.75, mouth: [50, 69], ms: [1.5, 1.1], ck: [[20, 68], [80, 68]],
  },

  // big white facial discs + oversized, mismatched geometric brows
  owl: {
    head: 'M18 32L10 6L36 20Q50 16 64 20L90 6L82 32C92 46 92 70 80 82C70 92 30 92 20 82C8 70 8 46 18 32Z',
    clip: [{ d: 'M30 86L36 80L42 86M44 90L50 84L56 90M58 86L64 80L70 86', f: 'none', w: 3, o: 0.5 }],
    front: [
      { c: [35, 51, 14], f: white, w: 3 },
      { c: [65, 51, 14], f: white, w: 3 },
      { d: 'M44 60L56 60L50 71Z', f: coral, w: 3 },
    ],
    top: [
      { d: 'M18 33L47 38L46 45L19 41Z', f: 'ink', w: 0 },
      { d: 'M53 40L80 26L83 33L55 47Z', f: 'ink', w: 0 },
      { c: [50, 27, 2.6], f: yellow, w: 2 },
    ],
    eyes: [[35, 52], [65, 52]], es: 0.95, mouth: [50, 78], ms: 0.6, ck: null,
  },

  // ink ears + eye patches, pink polka-dot bandana knotted on the side
  panda: {
    back: [{ c: [23, 28, 11], f: 'ink' }, { c: [77, 28, 11], f: 'ink' }],
    head: 'M50 20C76 20 92 36 92 58C92 80 74 90 50 90C26 90 8 80 8 58C8 36 24 20 50 20Z',
    clip: [
      { d: 'M0 30L100 14V30L0 46Z', f: pink, w: 0 },
      { d: 'M0 30L100 14M100 30L0 46', f: 'none', w: 3.2 },
      { c: [16, 35, 2.2], f: white, w: 0 },
      { c: [34, 31, 2.2], f: white, w: 0 },
      { c: [52, 28, 2.2], f: white, w: 0 },
      { c: [70, 25, 2.2], f: white, w: 0 },
      { c: [84, 22, 2.2], f: white, w: 0 },
    ],
    front: [
      { e: [33, 56, 10, 12.5, 30], f: 'ink', w: 0 },
      { e: [67, 56, 10, 12.5, -30], f: 'ink', w: 0 },
      { c: [34, 55, 7], f: white, w: 0 },
      { c: [66, 55, 7], f: white, w: 0 },
      { e: [50, 68, 4.5, 3.2], f: 'ink', w: 0 },
    ],
    top: [
      { d: 'M88 24L100 12L101 28Z', f: pink, w: 3 },
      { d: 'M88 26L99 36L91 40Z', f: pink, w: 3 },
      { c: [89, 25, 4.6], f: pink, w: 3 },
    ],
    eyes: [[34, 55], [66, 55]], es: 0.75, mouth: [50, 75], ms: 0.7, ck: [[20, 70], [80, 70]],
  },

  // bandit mask stripe, spiky cheek fluff, white muzzle
  raccoon: {
    back: [
      { d: 'M14 42L16 10L42 26Z' },
      { d: 'M58 26L84 10L86 42Z' },
      { d: 'M19 32L20 18L32 26Z', f: 'ink', w: 0 },
      { d: 'M68 26L80 18L81 32Z', f: 'ink', w: 0 },
    ],
    head: 'M50 22C70 22 84 32 88 44L98 56L87 60L94 72L78 74C72 86 62 90 50 90C38 90 28 86 22 74L6 72L13 60L2 56L12 44C16 32 30 22 50 22Z',
    clip: [
      { d: 'M0 42C20 36 36 46 50 46C64 46 80 36 100 42V58C82 56 66 64 50 62C34 64 18 56 0 58Z', f: 'ink', w: 0 },
      { d: 'M46 20H54L52 38H48Z', f: 'ink', w: 0 },
      { e: [50, 74, 16, 12], f: white, w: 0 },
    ],
    front: [
      { c: [35, 51, 7.2], f: white, w: 0 },
      { c: [65, 51, 7.2], f: white, w: 0 },
      { d: 'M45 65L55 65L50 70Z', f: 'ink', w: 2.5 },
    ],
    eyes: [[35, 51], [65, 51]], es: 0.78, mouth: [50, 77], ms: 0.7, ck: null,
  },

  // greaser pompadour quiff, big yellow bill
  duck: {
    head: 'M50 24C74 24 90 40 90 60C90 80 74 90 50 90C26 90 10 80 10 60C10 40 26 24 50 24Z',
    front: [
      { d: 'M28 66C28 56 72 56 72 66C72 76 62 80 50 80C38 80 28 76 28 66Z', f: yellow, w: 3.5 },
      { c: [45, 62, 1.6], f: 'ink', w: 0 },
      { c: [55, 62, 1.6], f: 'ink', w: 0 },
    ],
    top: [
      { d: 'M20 42C14 22 30 4 54 3C74 2 90 12 88 25C87 33 77 35 73 29C70 25 64 25 58 29C50 34 40 34 32 37C27 39 23 41 20 42Z', f: 'ink', w: 3 },
      { d: 'M28 24Q38 10 58 9', f: 'none', s: white, w: 2.6, o: 0.8 },
      { d: 'M78 18Q82 21 81 25', f: 'none', s: white, w: 2.2, o: 0.6 },
    ],
    eyes: [[35, 48], [65, 48]], mouth: [50, 70], ms: 0.8, ck: [[20, 64], [80, 64]],
  },

  // white "urajiro" cheeks + brow dots; idles with a side-eye smirk
  shiba: {
    back: [
      { d: 'M12 46L18 8L46 28Z' },
      { d: 'M54 28L82 8L88 46Z' },
      { d: 'M20 34L22 18L35 27Z', f: white, w: 0, o: 0.7 },
      { d: 'M65 27L78 18L80 34Z', f: white, w: 0, o: 0.7 },
    ],
    head: 'M50 24C72 24 88 36 90 54C92 74 74 90 50 90C26 90 8 74 10 54C12 36 28 24 50 24Z',
    clip: [{ d: 'M0 62C14 58 30 56 38 60Q50 52 62 60C70 56 86 58 100 62V100H0Z', f: white, w: 0 }],
    front: [
      { e: [36, 40, 4.4, 2.8, -10], f: white, w: 0 },
      { e: [64, 40, 4.4, 2.8, 10], f: white, w: 0 },
      { e: [50, 63, 4.8, 3.4], f: 'ink', w: 0 },
    ],
    eyes: [[36, 51], [64, 51]], mouth: [50, 72], ms: 0.85, ck: [[22, 62], [78, 62]],
    idle: { eyes: 'side', mouth: 'smirk' },
  },

  // frilly two-tone gills, wide-set dot eyes, freckles
  axolotl: {
    back: [
      { d: 'M22 48L5 31M13 39V29M13 39L3 41M22 72L6 87M14 79L4 77M14 79L15 90M78 48L95 31M87 39V29M87 39L97 41M78 72L94 87M86 79L96 77M86 79L85 90M19 60L1 60M9 60L3 52M9 60L3 68M81 60L99 60M91 60L97 52M91 60L97 68', f: 'none', w: 11 },
      { d: 'M22 48L5 31M13 39V29M13 39L3 41M22 72L6 87M14 79L4 77M14 79L15 90M78 48L95 31M87 39V29M87 39L97 41M78 72L94 87M86 79L96 77M86 79L85 90', f: 'none', s: pink, w: 4.6 },
      { d: 'M19 60L1 60M9 60L3 52M9 60L3 68M81 60L99 60M91 60L97 52M91 60L97 68', f: 'none', s: teal, w: 4.6 },
    ],
    head: 'M50 30C76 30 92 42 92 62C92 80 74 90 50 90C26 90 8 80 8 62C8 42 24 30 50 30Z',
    front: [
      { c: [44, 40, 1.8], f: 'ink', w: 0, o: 0.5 },
      { c: [50, 37, 1.8], f: 'ink', w: 0, o: 0.5 },
      { c: [56, 40, 1.8], f: 'ink', w: 0, o: 0.5 },
    ],
    eyes: [[29, 57], [71, 57]], es: 0.8, mouth: [50, 69], ms: [1.5, 1.1], ck: [[20, 70], [80, 70]],
  },

  // white heart face, yellow beak, coral polka-dot bow tie
  penguin: {
    head: 'M50 16C74 16 90 34 90 58C90 80 74 90 50 90C26 90 10 80 10 58C10 34 26 16 50 16Z',
    clip: [{ d: 'M50 42C58 30 82 32 82 52C82 72 68 88 50 88C32 88 18 72 18 52C18 32 42 30 50 42Z', f: white, w: 0 }],
    front: [{ d: 'M42 61L58 61L50 71Z', f: yellow, w: 3 }],
    top: [
      { d: 'M47 17Q42 6 52 2Q48 9 54 16', f: 'none', w: 3.5 },
      { d: 'M50 90L35 82V98ZM50 90L65 82V98Z', f: coral, w: 3.2 },
      { c: [50, 90, 4.2], f: coral, w: 3.2 },
      { c: [40, 90, 1.5], f: white, w: 0 },
      { c: [60, 90, 1.5], f: white, w: 0 },
    ],
    eyes: [[36, 53], [64, 53]], mouth: [50, 77], ms: 0.6, ck: [[26, 66], [74, 66]],
  },
}

// Shared status faces, drawn around (0,0) of each eye / the mouth.
// kind: 'fill' → ink fill, 'line' → ink stroke 4.2, 'glint' → white fill
export const EYES = {
  idle: [{ e: [0, 0, 4.6, 6], k: 'fill' }, { c: [1.6, -2.4, 1.7], k: 'glint' }],
  thinking: [{ e: [3, -4, 4.2, 5.4], k: 'fill' }, { c: [4.4, -6, 1.5], k: 'glint' }],
  working: [{ d: 'M-6 1Q0 -6 6 1', k: 'line' }],
  waiting: [{ c: [0, 0, 7], k: 'fill' }, { c: [2.5, -2.5, 2.4], k: 'glint' }],
  sleeping: [{ d: 'M-6 2Q0 6.5 6 2', k: 'line' }],
  error: [{ d: 'M-5 -5L5 5M5 -5L-5 5', k: 'line' }],
  // idle variant: unimpressed side-eye (heavy lid, pupil slid right)
  side: [{ d: 'M0.5 -1.5H7Q7 5 3.8 5Q0.5 5 0.5 -1.5Z', k: 'fill' }, { d: 'M-6.5 -1.5H7.5', k: 'line' }],
}
export const MOUTHS = {
  idle: [{ d: 'M-6 0Q0 5.5 6 0', k: 'line' }],
  thinking: [{ d: 'M-4 2Q0 0.5 4 2', k: 'line' }],
  working: [{ d: 'M-7 -2Q0 7 7 -2Z', k: 'fill' }],
  waiting: [{ e: [0, 2, 3.6, 4.4], k: 'fill' }],
  sleeping: [{ d: 'M-4 1Q0 3 4 1', k: 'line' }],
  error: [{ d: 'M-6 3Q0 -2 6 3', k: 'line' }],
  smirk: [{ d: 'M-6 1Q1 4.5 7 -2.5', k: 'line' }],
}

export const ANIMAL_KEYS = ['fox', 'cat', 'bunny', 'bear', 'frog', 'owl', 'panda', 'raccoon', 'duck', 'shiba', 'axolotl', 'penguin']
