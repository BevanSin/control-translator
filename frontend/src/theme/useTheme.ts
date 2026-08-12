import { useEffect, useState } from 'react'
import {
  applyTheme,
  persistThemePreference,
  readThemePreference,
  resolveTheme,
  watchSystemTheme,
  type ResolvedTheme,
  type ThemePreference,
} from './theme'

export function useTheme(): {
  preference: ThemePreference
  resolved: ResolvedTheme
  setPreference: (preference: ThemePreference) => void
} {
  const [preference, setPreferenceState] = useState<ThemePreference>(() => readThemePreference())
  const [resolved, setResolved] = useState<ResolvedTheme>(() => resolveTheme(preference))

  useEffect(() => {
    const next = resolveTheme(preference)
    setResolved(next)
    applyTheme(next)
    persistThemePreference(preference)
  }, [preference])

  useEffect(() => {
    if (preference !== 'system') {
      return undefined
    }
    return watchSystemTheme(() => {
      const next = resolveTheme('system')
      setResolved(next)
      applyTheme(next)
    })
  }, [preference])

  return { preference, resolved, setPreference: setPreferenceState }
}
