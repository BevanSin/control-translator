import '@testing-library/jest-dom/vitest'
import { expect, vi } from 'vitest'
import { toHaveNoViolations } from 'jest-axe'

expect.extend(toHaveNoViolations)

const listeners = new Set<EventListenerOrEventListenerObject>()
let prefersDark = false

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: vi.fn((query: string): MediaQueryList => ({
    matches: prefersDark,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn((_type: string, listener: EventListenerOrEventListenerObject) => {
      listeners.add(listener)
    }) as MediaQueryList['addEventListener'],
    removeEventListener: vi.fn((_type: string, listener: EventListenerOrEventListenerObject) => {
      listeners.delete(listener)
    }) as MediaQueryList['removeEventListener'],
    dispatchEvent: vi.fn(),
  })),
})

export function setSystemTheme(theme: 'light' | 'dark') {
  prefersDark = theme === 'dark'
  const event = { matches: prefersDark, media: '(prefers-color-scheme: dark)' } as MediaQueryListEvent
  listeners.forEach((listener) => {
    if (typeof listener === 'function') {
      listener(event)
    } else {
      listener.handleEvent(event)
    }
  })
}
