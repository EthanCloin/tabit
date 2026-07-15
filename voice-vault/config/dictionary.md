# Dictionary

Your personal vocabulary. This file does two jobs:

1. **Biases transcription** — every term below is fed to faster-whisper as `hotwords` /
   `initial_prompt`, which raises the probability the model spells it your way.
   (This is *soft* biasing, not a guarantee — see step 2 for the safety net.)
2. **Normalizes the transcript** — after transcription, each alias is rewritten to its
   canonical form. This is the deterministic layer that makes a term reliably correct in
   the final note even when the raw audio wobbled.

## Format

One entry per line:

```
hotword | alias, another alias — Canonical Form
```

- **hotword** — the token whisper should learn (an acronym or unusual spelling).
- **aliases** — comma-separated things you might say that should be rewritten. Optional.
- **Canonical Form** — how it should appear in the finished note (after the ` — `). Optional;
  defaults to the hotword.

Lines starting with `#` are comments. Blank lines are ignored.

## Entries

DDD | domain driven design, domain-driven design — Domain-Driven Design
OAuth | oauth, o auth, oauth two, oauth 2 — OAuth 2.0
OIDC | oidc, open id connect, openid connect — OpenID Connect
JWT | jwt, json web token — JWT
PKCE | pkce, pixie — PKCE
CSRF | csrf, cross site request forgery — CSRF
XSS | xss, cross site scripting — XSS
SPA | spa, single page app, single-page app — single-page app
MVC | mvc, model view controller — Model-View-Controller
CORS | cors — CORS
Bellhop | bell hop, bellhop — Bellhop
access token | accesstoken — AccessToken
refresh token | refreshtoken — RefreshToken
client secret | clientsecret, client-secret — client_secret
client id | clientid, client-id — client_id
