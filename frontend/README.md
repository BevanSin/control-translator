# control-translator frontend

Local-only React + TypeScript project dashboard for the authenticated loopback API.

## Development

```powershell
npm ci
npm run dev
npm run lint
npm run typecheck
npm test
npm run build
```

Start `ct-api` in another terminal and paste the printed session token into the bootstrap form. Development and preview servers proxy same-origin `/api` requests to `http://127.0.0.1:8756`, so the API can retain its empty CORS allow-list. An explicit API origin, when needed, is restricted to HTTP loopback addresses. The UI sends the token only in the `X-CT-Session-Token` header and keeps it in memory for the current tab; it is not persisted by the frontend.

The production build is static and uses only bundled/local assets.
