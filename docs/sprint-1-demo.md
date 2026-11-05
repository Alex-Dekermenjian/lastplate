# Sprint 1 demonstration (LP-15)

One browser tab, about 10 minutes. It follows the sprint stories in the order
agreed in LP-3: Team 3 (sign-in, profile, approval), Team 2 (publishing,
photos, stock), Team 1 (browsing, permissions), then LP-15 (integration).

All shops, people and offers are fictional demo data.

## Before the demo (2 minutes)

Docker Desktop must be running. From the repository folder:

```powershell
.\.venv\Scripts\python.exe scripts\seed_demo.py --password "<choose a demo password>"
```

It starts every service and loads fresh demo data with pickup times later
today. Run it again any time to reset the demo. Share the password only with
the team.

| Account | Email / username | Used for |
| --- | --- | --- |
| Approved bakery | `cedar@demo.lastplate.test` | Publishing and stock |
| Approved patisserie | `sunrise@demo.lastplate.test` | Extra offers |
| Pending shop | `newshop@demo.lastplate.test` | Approval |
| Team admin | `demo-admin` | Shop review (LP-9) |

## The walkthrough (all in one tab)

Start at **http://127.0.0.1:8000/demo/**, the demo guide. It lists every step
below (LP-7 to LP-15) with one-click buttons and the demo accounts.

### 1. The customer's view (LP-13): no account needed
1. Open http://127.0.0.1:8000/. Explain the idea: shops sell surplus food at a
   discount, and customers collect and pay at the shop.
2. Click **Browse available food**. Each card shows the discount, units left,
   pickup time (Today/Tomorrow, Lebanon time) and allergens.
3. Open **Butter croissants**. Show the price, pickup address, ingredients and
   allergens, and the **Call the shop** button.
4. Point out that only approved shops' published offers appear. Zaatar Corner
   is pending, so its food is not listed.

### 2. A shop joins (LP-7, LP-8): Team 3
1. In the header, click **Register your shop**. Register a new shop live, using
   a new email each run, such as `live1@demo.lastplate.test`.
2. The workspace shows **Pending review**.
3. Click **Edit shop profile**, fill in the address, area and phone, then save.
4. Click **Sign out**.

### 3. The team approves the shop (LP-9): Team 3
1. Open http://127.0.0.1:8002/admin/marketplace/shop/ and sign in as `demo-admin`.
   This is the Marketplace service's own admin, because each service runs
   separately.
2. Open the shop registered in step 2 (or **Zaatar Corner (demo)**), choose
   **Approved**, tick **I confirmed the applicant represents this shop**, and save.
3. Optional: show that rejecting without a reason is refused.
4. Go back to http://127.0.0.1:8000/.

### 4. The shop sells its surplus (LP-10, LP-11, LP-12): Team 2
1. **Sign in** as `cedar@demo.lastplate.test`.
2. In **Your food offers**, the **Cinnamon rolls** offer is a draft. Click
   **Publish offer**.
3. Click **Create food offer** and create one live with a photo, ingredients and
   allergens. Save the draft, then publish it.
4. On **Butter croissants**, enter `-2` in **Adjust stock** with the reason
   "Two boxes sold at the counter", then click **Update stock**.
5. Click **Browse food** in the header. The cinnamon rolls and the new offer
   are now visible, and the croissants show **4 left**.

### 5. The rules are enforced (LP-14): Team 1
- Create an offer with a discounted price higher than the original price:
  it is refused with a clear message.
- Sign out, then open http://127.0.0.1:8000/workspace/: you are sent to sign-in.
- Sign in as the pending shop (`newshop@…`) before approving it: it cannot publish.

### 6. Integration (LP-15)
1. On http://127.0.0.1:8000/, scroll to **System status**: Accounts,
   Marketplace and Reservations each report **ready**, each with its own database.
2. Show the automated checks: every service's tests run in GitHub Actions
   against PostgreSQL, plus a Docker Compose smoke check
   (`scripts\smoke_check.py`).
3. Still to do in LP-15: the shared HTTPS deployment
   ([hosting-plan.md](hosting-plan.md)). This demonstration runs locally with
   Docker Compose.

## Next sprint
Customer accounts and reservations (Sprint 2): customers reserve a quantity,
and the shop confirms collection and payment.
