import React from 'react'
import { createRoot } from 'react-dom/client'
import './i18n'
import App from './App'
import './styles.css'
import './polish.css'
import './themes.css'
import { initPalette } from './theme'

try {
  const t = localStorage.getItem('dot_theme')
  if (t) document.documentElement.dataset.theme = t
} catch { /* ignore */ }

initPalette()

createRoot(document.getElementById('root')).render(<App />)
