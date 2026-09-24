import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { domAnimation, LazyMotion, MotionConfig } from 'framer-motion'

import './styles/app.css'
import { App } from '@/App'
import { Crash } from '@/components/Crash'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* A render error shows a restart screen in the device's own voice, not
        a blank page (W5-4). */}
    <Crash>
      {/* `m` components and the smaller feature set, so Framer Motion costs
          the first paint a third of what `motion` does (W5-3). Layout and
          drag live in the lazy chunks that use them (the cards, the trace
          sheet), and `strict` stops a full `motion` import creeping back. */}
      <LazyMotion features={domAnimation} strict>
        {/* Every Framer Motion animation honours the device's reduced-motion
            setting, as in fanmoot (M15-3): transforms drop, opacity remains. */}
        <MotionConfig reducedMotion="user">
          <App />
        </MotionConfig>
      </LazyMotion>
    </Crash>
  </StrictMode>,
)
