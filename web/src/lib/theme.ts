/**
 * The theme actually in force, as "light" or "dark" — what a `ui://` view is
 * told in its host context (W2-6), so a card matches the screen it sits on.
 *
 * Same three states as tokens.css: `data-theme` on <html> when the visitor
 * chose, the OS preference when they did not.
 */
import { useSyncExternalStore } from 'react'

export type Theme = 'light' | 'dark'

function read(): Theme {
  const chosen = document.documentElement.getAttribute('data-theme')
  if (chosen === 'light' || chosen === 'dark') return chosen
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function subscribe(notify: () => void): () => void {
  const media = window.matchMedia('(prefers-color-scheme: dark)')
  const observer = new MutationObserver(notify)
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] })
  media.addEventListener('change', notify)
  return () => {
    observer.disconnect()
    media.removeEventListener('change', notify)
  }
}

export function useTheme(): Theme {
  return useSyncExternalStore(subscribe, read, () => 'dark')
}
