export type ThemePreference = 'system' | 'light' | 'dark'
export type ResolvedTheme = 'light' | 'dark'

export const THEME_STORAGE_KEY = 'ct-theme-preference'
const DARK_QUERY = '(prefers-color-scheme: dark)'

export function readThemePreference(storage: Storage = window.localStorage): ThemePreference {
  const stored = storage.getItem(THEME_STORAGE_KEY)
  return stored === 'light' || stored === 'dark' ? stored : 'system'
}

export function persistThemePreference(preference: ThemePreference, storage: Storage = window.localStorage): void {
  if (preference === 'system') {
    storage.removeItem(THEME_STORAGE_KEY)
    return
  }
  storage.setItem(THEME_STORAGE_KEY, preference)
}

export function resolveTheme(preference: ThemePreference, matchMedia: Window['matchMedia'] = window.matchMedia): ResolvedTheme {
  if (preference !== 'system') {
    return preference
  }
  return matchMedia(DARK_QUERY).matches ? 'dark' : 'light'
}

export function applyTheme(resolved: ResolvedTheme, root: HTMLElement = document.documentElement): void {
  root.dataset.theme = resolved
  root.style.colorScheme = resolved
}

export function watchSystemTheme(onChange: () => void, matchMedia: Window['matchMedia'] = window.matchMedia): () => void {
  const query = matchMedia(DARK_QUERY)
  query.addEventListener('change', onChange)
  return () => query.removeEventListener('change', onChange)
}
