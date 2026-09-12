# Deploying

**Backend first, frontend second.** Next bakes `NEXT_PUBLIC_API_BASE` into the bundle at
build time, so the frontend has to know the backend's address before it is built.

Total: about 40 minutes, most of it waiting.

---

## The thing that will bite you

Vercel serves the frontend over **https**. A browser refuses to let an https page call an
**http** backend — that's the mixed-content rule, and it applies to the `EventSource`
connection this entire app hangs off. `http://<your-ip>:8000` will not work from a
deployed frontend. It will fail in the console and look, from the outside, exactly like
agents that went quiet.

So the backend needs a certificate. Caddy gets one free and automatic, with no domain
purchase, because `<your-ip>.sslip.io` resolves to `<your-ip>`. That is what
[`Caddyfile`](Caddyfile) and [`vultr-setup.sh`](vultr-setup.sh) set up.

---

## 1. Push

Vercel builds from GitHub, so anything uncommitted or unpushed does not exist as far as
it is concerned.

```powershell
git push
```

## 2. Backend → Vultr

Create the instance: **Cloud Compute → Shared CPU → Ubuntu 24.04 → the smallest plan.**
It's a demo; 1 vCPU / 1 GB is plenty. Note the IP.

```bash
ssh root@<your-vultr-ip>
curl -fsSL https://raw.githubusercontent.com/veriivy/synesis/main/deploy/vultr-setup.sh -o setup.sh
bash setup.sh <your-vultr-ip>
```

That installs Python and Caddy, clones the repo, creates an unprivileged `synesis` user,
installs both Python halves, registers the systemd service, gets a TLS certificate, and
closes every port except SSH, 80 and 443.

Then add your keys, which never travel through a script:

```bash
nano /opt/synesis/.env       # ANTHROPIC_API_KEY, OPENAI_API_KEY, IFM_API_KEY
systemctl restart synesis
```

Check it:

```bash
curl https://<your-ip>.sslip.io/health
```

You want `{"ok": true, ...}` with a real path in the `git` field. If `git` says
`MISSING`, the clone and commit steps will fail later even though the server starts.

## 3. Frontend → Vercel

1. vercel.com → **Add New → Project** → import `veriivy/synesis`.
2. **Root Directory: `web`.** This is the setting that matters — without it Vercel looks
   at the repo root, finds no `package.json`, and fails.
3. Framework preset: Next.js. It detects this on its own.
4. **Environment Variables** → add `NEXT_PUBLIC_API_BASE` = `https://<your-ip>.sslip.io`
   (no trailing slash).
5. Deploy.

## 4. Close the loop

Vercel hands you a URL. The backend has never heard of it, and will reject its requests
until you say so:

```bash
nano /opt/synesis/.env
# CORS_ORIGINS=http://localhost:3000,https://your-app.vercel.app
systemctl restart synesis
```

Open the Vercel URL and run replay mode end to end. If the transcript renders and the
refusal shows up, both halves are live.

---

## When something is wrong

| Symptom | Cause |
|---|---|
| Browser console: "blocked… mixed content" | `NEXT_PUBLIC_API_BASE` is `http://`. It must be `https://`. |
| Console: "No 'Access-Control-Allow-Origin'" | The Vercel URL is not in `CORS_ORIGINS`. Add it, restart. |
| Changed the env var on Vercel, still broken | `NEXT_PUBLIC_*` is baked in at **build** time. **Redeploy**, don't restart. |
| Events arrive in a clump, not live | Response buffering. Check `flush_interval -1` in the Caddyfile. |
| Health is fine, nothing else works | `journalctl -u synesis -f`, then try the request again and watch. |
| Certificate never issues | `journalctl -u caddy -f`. Usually 80/443 blocked or the IP in the Caddyfile is wrong. |

## Redeploying after a push

```bash
cd /opt/synesis && git pull && systemctl restart synesis
```

Vercel redeploys itself on every push to `main`.

---

## Honest status

These files have **not** been run against a real Vultr instance — I have no box to test
on. Every step is standard and each one fails loudly and locally if it fails at all, but
read `vultr-setup.sh` before running it rather than after.
