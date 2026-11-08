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
