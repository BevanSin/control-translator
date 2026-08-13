import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

function consumeBootstrapToken(): string {
  const fragment = new URLSearchParams(window.location.hash.slice(1))
  const token = fragment.get('ct-session-token') ?? ''
  if (token) {
    window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}`)
  }
  return token
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App bootstrapToken={consumeBootstrapToken()} />
  </StrictMode>,
)
