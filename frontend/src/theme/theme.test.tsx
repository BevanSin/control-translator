import { afterEach, describe, expect, it } from 'vitest'
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from '../App'
import { THEME_STORAGE_KEY } from './theme'
import { setSystemTheme } from '../test/setup'

describe('theme preference', () => {
  afterEach(() => {
    localStorage.clear()
    document.documentElement.removeAttribute('data-theme')
    document.documentElement.style.colorScheme = ''
  })

  it('defaults to the system theme without persisting a choice', async () => {
    setSystemTheme('dark')

    render(<App />)

    await screen.findByRole('heading', { name: /local project dashboard/i })
    expect(document.documentElement).toHaveAttribute('data-theme', 'dark')
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBeNull()
  })

  it('persists an explicit theme and clears storage when returning to system', async () => {
    setSystemTheme('light')
    const user = userEvent.setup()
    render(<App />)

    await user.selectOptions(screen.getByLabelText(/theme preference/i), 'light')

    expect(document.documentElement).toHaveAttribute('data-theme', 'light')
    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe('light')

    await user.selectOptions(screen.getByLabelText(/theme preference/i), 'system')

    expect(localStorage.getItem(THEME_STORAGE_KEY)).toBeNull()
  })
})
