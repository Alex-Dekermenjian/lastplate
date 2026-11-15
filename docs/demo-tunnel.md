# Demo on a phone through a Cloudflare quick tunnel (LP-46)

A quick tunnel gives the local site a temporary public link,
`https://<random-words>.trycloudflare.com`, that forwards to
`http://localhost:8000`. Anyone with the link can open LastPlate from a phone.
There's no Cloudflare account and no router setup.

Only the web service (port 8000) is shared. Photos are served by the web
service at `/media/...`, and it fetches them from Marketplace inside Docker.
The other services' ports stay private.

## One-time setup

1. Install cloudflared (Windows):

   ```powershell
   winget install --id Cloudflare.cloudflared
   ```

   Open a new terminal afterwards so `cloudflared` is on the PATH.

2. If `compose.env` does not exist yet, follow [local-setup.md](local-setup.md)
   (it runs `.\.venv\Scripts\python.exe scripts\init_dev_env.py`).

## Start the demo

Run these from the repository root.

1. Start the stack and wait until every service is healthy:

   ```powershell
   docker compose --env-file compose.env up -d --build --wait
   ```

2. Check that http://localhost:8000/ opens on this computer.

3. Start the tunnel and leave this terminal open:

   ```powershell
   cloudflared tunnel --url http://localhost:8000
   ```

4. After a few seconds cloudflared prints a box like this:

   ```
   |  Your quick Tunnel has been created! Visit it at (it may take some time to be reachable):  |
   |  https://quiet-river-demo.trycloudflare.com                                                |
   ```

   Copy that `https://...trycloudflare.com` link and open it on the phone.
   If the link doesn't load yet, wait 10–20 seconds and reload.

5. Create the admin account if you don't have one yet. It asks for the
   password twice:

   ```powershell
   docker compose --env-file compose.env exec accounts python manage.py create_admin --email admin@example.test --name "LastPlate Admin"
   ```

   Log in at `<link>/login/` and you land on `/console`.

## Orders on the link (LP-85)

Reservations and its timer job are part of the stack, so reserving, My orders,
Today's orders, Paid and expiry all work through the link with nothing extra
to deploy. Their secret keys stay in `compose.env` on the laptop running the
demo, never in GitHub.

**Before the demo (the "redeploy")**: after each merge, the laptop that runs
the demo takes the new code:

```powershell
git switch main
git pull
docker compose --env-file compose.env up -d --build --wait
```

**Show expiry live**: orders are held 60 minutes by default. For the demo, add
this line to `compose.env` and run the `up` command again. Reservations and the
timer job restart with a 2-minute hold:

```
PICKUP_HOLD_MINUTES=2
```

An order that isn't marked Paid within 2 minutes then shows Expired (within
about a minute; the timer job runs every 60 seconds), and its food is back on
sale. Remove the line and run `up` again to return to 60 minutes.

**Check the flow through the link**: with the tunnel open, run this in a
second terminal. It opens every page through the link like a phone would:
a fictional restaurant signs up, is approved in `/console` and publishes an
offer; a fictional customer reserves it; the restaurant marks it Paid; a second
order expires and its food goes back on sale. It also checks that the link
doesn't reach the Django admin, `/internal/` or any service API. It adds
FLOWCHECK-labelled fictional data and deletes nothing. It takes about a minute.

```powershell
.\.venv\Scripts\python.exe "scripts\order flow check.py" --url https://<words>.trycloudflare.com
```

It should end with `All order flow checks passed.` Without `--url` it checks
`http://127.0.0.1:8000`.

**Sign-up limit**: Accounts allows 10 sign-ups per hour per connection, and
every visitor reaches Accounts through the web service, so **all phones on the
link share those 10**. Each run of the check uses 2. If sign-up says "Too many
registration attempts", restarting Accounts resets the count (it is kept in
memory):

```powershell
docker compose --env-file compose.env restart accounts
```

Create the demo accounts before the demo rather than live.

At the end the check marks its offer sold out, so nothing FLOWCHECK stays on
the public offers page. Its fictional accounts and orders stay (orders are
never deleted, LP-17); they belong to its own restaurant and customer, so the
demo accounts don't see them.

## Stop the demo

1. Press **Ctrl+C** in the cloudflared terminal. The link stops working at once.
2. Stop the stack and keep its data:

   ```powershell
   docker compose --env-file compose.env down
   ```

   Never add `-v`, because it deletes the databases and photos.

## Good to know

- The link is new every time cloudflared starts, so send the current one.
- The link is public while the tunnel runs, and the local stack runs with
  `DJANGO_DEBUG=True`. Use only fictional demo data, and stop the tunnel after the demo.
- The web service accepts any `*.trycloudflare.com` host and trusts the
  tunnel's `X-Forwarded-Proto: https` header, so logins and forms work over
  https. `localhost` and `127.0.0.1` keep working as before. To allow
  another host, add it to `DJANGO_ALLOWED_HOSTS` and its `https://` origin to
  `DJANGO_CSRF_TRUSTED_ORIGINS` in `compose.yaml`.
- The developer-only Django admin (`127.0.0.1:8001/admin/`, `127.0.0.1:8002/admin/`)
  is not shared through the tunnel. Review restaurants in `/console` instead.
