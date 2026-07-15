<!-- GOLD-STANDARD EXAMPLE — this file is a style anchor, not a real note.
     Synthesis is shown the notes in this folder as few-shot examples of the voice and
     structure to imitate. Curate a few of your best notes here. -->

OAuth 2.0 is a delegation framework: it lets a third-party app act on behalf of a user without
learning the user's identity. It's about **authorization**, not authentication — the end result
of any flow is an AccessToken that permits actions, and says nothing about *who* the user is.
For identity you layer [[OpenID Connect]] on top.

# The three parties

- **Client** — the app that wants access to resources.
- **Authorization Server** — issues Access/RefreshTokens after the user grants access.
- **Resource Server** — holds the user's data and checks the AccessToken on each request.

# Authorization Code grant

The common server-side flow. The user authorizes and is redirected back with a temporary
`code`, which the app exchanges for an AccessToken. All services should layer [[PKCE]] on top
to protect the exchange.

# Handling tokens

- Put the AccessToken in the `Authorization` header, prefixed with `Bearer ` — treat it as
  opaque, don't parse it.
- A stolen RefreshToken is more dangerous than a stolen AccessToken, so rotate them: each
  RefreshToken is single-use, and the server issues a new one every refresh.
- [[XSS]] is the main threat for a [[single-page app]]; short token lifetimes reduce the blast
  radius. (If you have a backend that can hold a secret, let it run the flow instead.)

See also: [[Authentication]], [[Bellhop]]
