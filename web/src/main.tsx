import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { MotionConfig } from 'framer-motion'

import './styles/app.css'
import { App } from '@/App'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* Every Framer Motion animation honours the device's reduced-motion
        setting, as in fanmoot (M15-3): transforms drop, opacity remains. */}
    <MotionConfig reducedMotion="user">
      <App />
    </MotionConfig>
  </StrictMode>,
)
