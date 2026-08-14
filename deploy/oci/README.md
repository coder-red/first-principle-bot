# Deploying to Oracle Cloud (Always Free ARM)

An Ampere A1 instance fixes the two things the free tier of a sleeping PaaS
gets wrong for this app:

- **It never sleeps.** Provider cooldowns stay warm. On a host that sleeps, the
  first request after every wake-up pays for a call to a provider whose daily
  allowance is already spent — the one failure mode here that costs real money.
- **It has a real disk.** `STATE_DB` finally means something: cooldowns, rate
  limits and the explore pools survive a restart.

It also matches the app's architecture rather than fighting it. `render.yaml`
pins `numInstances: 1` because the explore pools, the limiters and the
cooldowns all assume one process; a single VM satisfies that by construction.

```
                 ┌─────────────── OCI Ampere A1 (aarch64) ───────────────┐
  visitor ──443──┤ nginx ── 127.0.0.1:8000 ── uvicorn (systemd, fpb user) │
                 │   │                              │                     │
                 │   └─ X-Forwarded-For = real peer  └─ /var/lib/fpb/state.db
                 └───────────────────────────────────────────────────────┘
                                        │
                                        └── https ──► groq / gemini / openrouter
```

Files here: [`fpb.service`](fpb.service) · [`nginx.conf`](nginx.conf) ·
[`fpb.env.example`](fpb.env.example) · [`update.sh`](update.sh)

---

## 1. The instance

Console → Compute → Instances → Create.

| Setting | Value |
| --- | --- |
| Shape | **VM.Standard.A1.Flex**, 1 OCPU / 6 GB |
| Image | Ubuntu 24.04 (ships Python 3.12; the app needs 3.10+) |
| Boot volume | 50 GB default is plenty |
| SSH | Paste your public key — there is no password login |

The Always Free allowance is 4 OCPU and 24 GB of A1 total, so 1/6 leaves room
for three more instances. This app is I/O-bound waiting on provider APIs; one
core is not the constraint.

> **"Out of host capacity."** A1 is frequently exhausted in popular regions.
> Retry, try another availability domain, or pick a different home region — an
> Always Free account cannot change its home region afterwards, so choose one
> with capacity before you commit.

Note the public IP when it comes up.

## 2. Open the ports — *both* firewalls

This is the OCI trap that costs people an afternoon: opening the VCN is not
enough, because the Ubuntu image ships iptables rules that drop everything
except SSH. Miss the second half and the port is open at the cloud edge and
closed on the host, which looks exactly like nginx being broken.

**Cloud side** — VCN → Security Lists → your subnet's list → Add Ingress Rules.
Source `0.0.0.0/0`, IP Protocol TCP, destination ports **80** and **443**.

**Host side** — SSH in (`ssh ubuntu@<public-ip>`), then:

```bash
sudo iptables -L INPUT --line-numbers | head        # find the REJECT rule's number
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 80  -j ACCEPT
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

Insert *before* the `REJECT all` line — on the stock image that is position 6.
On Oracle Linux it is firewalld instead:

```bash
sudo firewall-cmd --permanent --add-service=http --add-service=https && sudo firewall-cmd --reload
```

## 3. DNS

An `A` record for your domain pointing at the public IP. Do this before
certbot — Let's Encrypt validates over HTTP and will fail on a name that does
not resolve yet. Confirm with `dig +short fpb.example.com`.

## 4. Install

```bash
sudo apt update && sudo apt install -y python3-venv python3-dev git nginx

# A service account with no login shell and no home directory to protect.
sudo useradd --system --no-create-home --shell /usr/sbin/nologin fpb

sudo install -d -o fpb -g fpb /opt/fpb /var/lib/fpb
sudo -u fpb git clone https://github.com/coder-red/first-principle-bot.git /opt/fpb

cd /opt/fpb
sudo -u fpb python3 -m venv .venv
sudo -u fpb .venv/bin/pip install --upgrade pip
sudo -u fpb .venv/bin/pip install -r requirements.txt
```

`uvicorn[standard]` pulls `uvloop`, `httptools` and `watchfiles`, all of which
publish aarch64 wheels — nothing compiles. If pip ever falls back to building
one, `python3-dev` is already installed above.

`/var/lib/fpb` is the only path the service can write to, which is why it is
created here rather than left to the app.

## 5. Configure

```bash
sudo install -d -m 750 -o root -g fpb /etc/fpb
sudo install -m 640 -o root -g fpb deploy/oci/fpb.env.example /etc/fpb/fpb.env
sudo nano /etc/fpb/fpb.env
```

Config lives outside `/opt/fpb` so a `git pull` cannot touch it and a stray
`git add` cannot stage it. Mode `640 root:fpb` means the service reads it and
nothing else does.

**Set at minimum:**

```bash
openssl rand -base64 32          # → APP_ACCESS_TOKEN
```

- `APP_ACCESS_TOKEN` — without it the deploy is anonymous and anyone who finds
  the domain spends your provider credits.
- `PUBLIC_URL=https://fpb.example.com` — the access cookie is only marked
  `Secure` when this starts with `https`, so a wrong value sends the shared
  token in the clear.
- `RELOAD=0` — the default is `1`, and the reloader forks a child systemd does
  not track.
- At least one provider's `_API_KEY` and `_MODEL`.

## 6. Start it

```bash
sudo install -m 644 /opt/fpb/deploy/oci/fpb.service /etc/systemd/system/fpb.service
sudo systemctl daemon-reload
sudo systemctl enable --now fpb

curl -s http://127.0.0.1:8000/api/health | python3 -m json.tool
```

Expect `"access_control": "token"` and `"persistence": "sqlite"`. If either
says `open` or `memory`, the env file did not load — `systemctl status fpb`
and `journalctl -u fpb -n 50`.

Also check the `providers` list. A provider missing from it has an unset key or
model and was skipped silently, which is by design (you can list one before
signing up) and is the most common misconfiguration.

## 7. nginx and TLS

```bash
sudo cp /opt/fpb/deploy/oci/nginx.conf /etc/nginx/sites-available/fpb
sudo sed -i 's/fpb\.example\.com/YOUR-DOMAIN/g' /etc/nginx/sites-available/fpb
sudo ln -sf /etc/nginx/sites-available/fpb /etc/nginx/sites-enabled/fpb
sudo rm -f /etc/nginx/sites-enabled/default
```

The config references certificate paths that do not exist yet, so `nginx -t`
will fail until certbot has run. Get the certificate first:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d YOUR-DOMAIN
sudo nginx -t && sudo systemctl reload nginx
```

Certbot installs its own renewal timer; `systemctl list-timers | grep certbot`
confirms it. The port-80 server block is kept deliberately — renewal needs it.

Read [`nginx.conf`](nginx.conf) before changing it. Three things in it are
load-bearing:

- **`proxy_set_header X-Forwarded-For $remote_addr;`** — overwrite, never
  `$proxy_add_x_forwarded_for`. `fpb/limits.py` keys every limiter on the first
  entry of that header, so appending would let a caller send their own value
  and land in a fresh bucket on every request, defeating all three limiters
  including the one guarding the access-token exchange. This line is what makes
  the per-IP limits mean anything.
- **`proxy_buffering off;`** with a 120s read timeout — `/api/chat/stream`
  emits NDJSON for 25-35s, and buffering hands it over in one piece, which
  defeats the endpoint. The timeout must clear `DECK_TIMEOUT_SECONDS` (90).
- **`client_max_body_size 256k;`** — `ChatRequest` permits a ~1.2 MB body and
  forwards 20 history messages to the model, so an uncapped body is ~100 000
  billed tokens on a request the caller controls.

## 8. Verify

```bash
# TLS, and the app behind it
curl -s https://YOUR-DOMAIN/api/health | python3 -m json.tool

# The gate: no token → 401
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://YOUR-DOMAIN/api/chat \
     -H 'content-type: application/json' -d '{"message":"why is the sky blue"}'

# With it → a deck (slow; this is a real model call)
curl -s -X POST https://YOUR-DOMAIN/api/chat \
     -H 'content-type: application/json' -H "x-access-token: YOUR-TOKEN" \
     -d '{"message":"why is the sky blue"}' | head -c 400

# Streaming actually streams — lines should appear over ~30s, not all at once
curl -N -X POST https://YOUR-DOMAIN/api/chat/stream \
     -H 'content-type: application/json' -H "x-access-token: YOUR-TOKEN" \
     -d '{"message":"why do metals conduct"}'

# X-Forwarded-For is NOT trusted from the client: spoofing it must not reset
# the limiter. Run this past DECK_RATE_LIMIT and it should still start 429ing.
for i in $(seq 1 12); do
  curl -s -o /dev/null -w "%{http_code} " -X POST https://YOUR-DOMAIN/api/chat \
       -H 'content-type: application/json' -H "x-access-token: YOUR-TOKEN" \
       -H "X-Forwarded-For: 10.0.0.$i" -d '{"message":"test"}'
done; echo
```

That last one is the check worth running. If those all return 200, nginx is
appending rather than overwriting and the rate limiter is decorative.

Then open `https://YOUR-DOMAIN` — you should get the access-token gate, and the
deck UI after unlocking.

## Operating it

```bash
journalctl -u fpb -f                    # live logs
journalctl -u fpb -p warning -n 100     # just the warnings: provider errors,
                                        # rate limits, unverified chains
sudo systemctl restart fpb
sudo /opt/fpb/deploy/oci/update.sh      # pull, reinstall, restart, health-check
```

`update.sh` rolls back to the previous commit if the health check fails, so a
bad pull leaves a running service.

**Watch `/api/health`.** `counters` shows decks served verified vs unverified,
how often the repair pass fired and whether it worked, per-provider errors, and
rate-limit refusals. `cooling` shows which providers are sidelined and until
when. Counters reset on restart — they are for reading off a running deploy.

**Back up the state.** It is a few kilobytes and it is the difference between a
restart being free and a restart costing a round of exhausted-provider calls:

```bash
sudo sqlite3 /var/lib/fpb/state.db ".backup '/var/lib/fpb/state-backup.db'"
```

Use `.backup` rather than `cp` — the database runs in WAL mode, and copying the
file alone can catch it mid-write.

**Set a budget alarm** in the OCI console. Always Free should never bill, but a
misconfigured shape silently rolling onto a paid tier is the standard way
people discover otherwise.

## Costs

The instance is free indefinitely. What you pay for is provider tokens, and
`APP_ACCESS_TOKEN` is the control on that — the rate limits only bound the
rate. The 10 TB/month egress allowance is not a concern for an app that serves
a few kilobytes of JSON per answer.

## If something is wrong

| Symptom | Cause |
| --- | --- |
| Connection times out | Host iptables. The VCN rule alone is not enough — step 2. |
| 502 from nginx | `systemctl status fpb`. Usually a bad `/etc/fpb/fpb.env`. |
| Health says `"persistence": "memory"` | `STATE_DB` unset, or `/var/lib/fpb` not writable by `fpb`. |
| Health says `"access_control": "open"` | `APP_ACCESS_TOKEN` is empty. Anyone can spend your credits. |
| `providers: []` | No provider has both a key and a model set. Incomplete ones are skipped by design. |
| Every deck fails | The model does not support `response_format={"type":"json_object"}`. Check with `python tools/check_providers.py`. |
| Stream arrives all at once | `proxy_buffering` got turned back on. |
| Login prompt never sticks | `PUBLIC_URL` is not `https://…`, so the cookie is not marked `Secure`. |
| Service restarts in a loop | `RELOAD` is not `0`; systemd is tracking the reloader's parent. |
