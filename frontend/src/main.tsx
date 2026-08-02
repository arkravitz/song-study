import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { App } from './App'
import { AppBoundary } from './components/ErrorBoundary'
import './styles.css'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* Without this, any render-time throw blanks the page with no way back.
        It has already cost one debugging session. */}
    <AppBoundary>
      <App />
    </AppBoundary>
  </StrictMode>,
)
