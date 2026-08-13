export function consumeBootstrapToken(): string {
  const fragment = new URLSearchParams(window.location.hash.slice(1))
  const token = fragment.get('ct-session-token') ?? ''
  if (token) {
    window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}`)
  }
  return token
}
