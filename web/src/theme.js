// Colour schemes: each one swaps the accent palette, the paper pattern behind
// everything and the shapes that float in the corners. Light / dark is separate
// (data-theme); the scheme is data-palette, remembered per browser.
import { useEffect, useState } from 'react'

export const PALETTES = [
  { key: 'memphis', swatch: ['#ff6b57', '#ffd23f', '#2ec4b6'] },
  { key: 'ocean', swatch: ['#1f7ae0', '#5ad1e6', '#ffc94d'] },
  { key: 'matcha', swatch: ['#3c9d5d', '#c6e36b', '#ff9f68'] },
  { key: 'berry', swatch: ['#d6457d', '#b18cff', '#ffe066'] },
  { key: 'noir', swatch: ['#111111', '#f2f2f2', '#ff3b30'] },
]

export function getPalette() {
  return document.documentElement.dataset.palette || 'memphis'
}

export function setPalette(key) {
  if (key === 'memphis') delete document.documentElement.dataset.palette
  else document.documentElement.dataset.palette = key
  try { localStorage.setItem('dot_palette', key === 'memphis' ? '' : key) } catch { /* private mode */ }
  window.dispatchEvent(new CustomEvent('dot:palette', { detail: key }))
}

export function initPalette() {
  try {
    const p = localStorage.getItem('dot_palette')
    if (p) document.documentElement.dataset.palette = p
  } catch { /* private mode */ }
}

export function usePalette() {
  const [p, setP] = useState(getPalette())
  useEffect(() => {
    const on = (e) => setP(e.detail)
    window.addEventListener('dot:palette', on)
    return () => window.removeEventListener('dot:palette', on)
  }, [])
  return p
}
