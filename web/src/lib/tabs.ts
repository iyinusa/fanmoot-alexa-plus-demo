/**
 * Arrow keys for a `role="tablist"` (W5-5), as the ARIA pattern expects:
 * Left and Right (or Up and Down) move between tabs, Home and End jump to
 * the ends, and the tab reached is selected at once. Put it on the tablist's
 * `onKeyDown`, and give only the selected tab `tabIndex={0}` so Tab itself
 * moves past the list rather than through every tab in it.
 */
import type { KeyboardEvent } from 'react'

const STEP: Record<string, number> = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }

export function onTabKeys(event: KeyboardEvent<HTMLElement>) {
  const tabs = [...event.currentTarget.querySelectorAll<HTMLElement>('[role="tab"]:not([disabled])')]
  const at = tabs.indexOf(document.activeElement as HTMLElement)
  if (at < 0) return
  const next =
    event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : STEP[event.key] !== undefined ? at + STEP[event.key]! : null
  if (next === null) return
  event.preventDefault()
  const tab = tabs[(next + tabs.length) % tabs.length]
  tab?.focus()
  tab?.click()
}
