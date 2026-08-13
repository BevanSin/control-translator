import { describe, expect, it } from 'vitest'
import { consumeBootstrapToken } from './bootstrap'

describe('launcher bootstrap', () => {
  it('consumes the token from the fragment and immediately removes the fragment', () => {
    window.history.replaceState(null, '', '/?mode=local#ct-session-token=fragment-only-token')

    expect(consumeBootstrapToken()).toBe('fragment-only-token')
    expect(window.location.pathname).toBe('/')
    expect(window.location.search).toBe('?mode=local')
    expect(window.location.hash).toBe('')
  })

  it('leaves unrelated fragments untouched', () => {
    window.history.replaceState(null, '', '/#project-dashboard')

    expect(consumeBootstrapToken()).toBe('')
    expect(window.location.hash).toBe('#project-dashboard')
  })
})
