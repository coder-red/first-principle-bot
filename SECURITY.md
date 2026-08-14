# Security

## Reporting a vulnerability

Please do not open a public issue.

Use GitHub's [private vulnerability
reporting](https://github.com/coder-red/first-principle-bot/security/advisories/new),
or email **mohammedbabatunde2001@gmail.com**.

Include what you did, what happened, and what you expected. A minimal
reproduction is worth more than a scanner report. This is a side project
maintained by one person — expect a first reply within a week, and no bounty.

## What this project is

A single-process FastAPI app that turns a question into one model call and
renders the result. There is no user database, no session store, no uploads,
and nothing persisted about a visitor beyond an optional rate-limit row keyed
on an IP.

**The asset worth protecting is your model credits.** Almost every risk below
is a variation on "a stranger spends your money".

## The threat model, honestly

### The endpoints that cost money are open by default

`/api/chat`, `/api/chat/stream` and `/api/explore/<sector>` each trigger a
model call. Unset, `APP_ACCESS_TOKEN` leaves all three anonymous. `HOST`
defaults to `127.0.0.1` precisely so that an unconfigured instance is not
reachable from anywhere else.

**If you expose the port, set `APP_ACCESS_TOKEN`.** It is a single shared
secret, not user accounts, and it answers the only question that matters on a
public URL: should this person be able to spend your credits at all.

### Rate limits are a speed bump, not a wall

Per-IP limits bound how *fast* a stranger spends your allowance, not whether
they can. They are keyed on the first entry of `X-Forwarded-For` when that
header is present, because behind a proxy the socket peer is the proxy.

That header is set by the client. **If your instance is reachable without a
proxy in front of it — or behind a proxy that appends rather than replaces
`X-Forwarded-For` — a caller can vary the header per request and each request
lands in a fresh bucket.** All three limiters are affected, including the one
in front of the access-token exchange.

Deploy behind a proxy that overwrites `X-Forwarded-For` with the real peer
(Render, Fly, Cloudflare and nginx with `real_ip_header` all do), and treat the
access token, not the limiter, as the actual control.

### Keys

Provider keys are read from the environment and never leave the server.
`render.yaml` marks every key `sync: false` so it is set in the dashboard, not
committed. `.env` is gitignored.

`/api/health` reports which providers are configured and which model each uses
— never a key. It is deliberately unauthenticated, because a platform health
check calls it and a 401 there takes the service down. If the provider chain
and the counters are more than you want public, put the path behind your proxy.

`DEBUG_ERRORS` sends raw provider exceptions to the browser. Those carry model
names, endpoint URLs and occasionally an echo of the request. Leave it off in
public; failures are logged either way.

### Model output in the browser

The whole UI is built by creating DOM nodes and assigning `textContent`. No
model output — and no visitor input — ever passes through `innerHTML`. Keep it
that way: a deck is attacker-influenced text, since the question that produced
it came from whoever is using the app.

### Prompt injection

A visitor controls the question, and the question goes to the model. Injected
instructions can make a deck say anything a model can be persuaded to say. The
chain validator checks *form* — one bedrock, levels increasing, no `ATOMIC`
descent card — and cannot check truth. Treat deck content as untrusted output.
Nothing in this app acts on it: there are no tools, no file access, and no
outbound calls driven by model output.

### Denial of service

Not in scope. One process, one instance, in-memory state. `DECK_TIMEOUT_SECONDS`
bounds a stalled provider and the request fields are length-capped, but a
determined caller can keep the box busy. Put it behind something that does DoS
protection if that matters to you.

## Supported versions

The tip of `main`. There are no maintained release branches; fixes ship there.
