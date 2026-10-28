# LastPlate

LastPlate is an EECE430 student website where bakeries and similar shops publish discounted surplus food. Customers reserve a quantity, collect it during the stated pickup window and pay directly at the shop.

## Current status

The Jira backlog and initial repository are prepared. The Django application is not implemented yet. The features below describe the agreed release plan, not completed functionality.

## Release plan

| Sprint | Working result |
| --- | --- |
| 1: Publish and browse | An approved shop publishes food, manages available stock and visitors inspect offers. |
| 2: Reserve and collect | Customers reserve food; the shop sees reservations and confirms payment and collection. |
| 3: Cancellations and release | Complete cancellation and recovery flows, test with users, verify the application and prepare the final demonstration. |

[Jira backlog](https://alexdekermen05.atlassian.net/jira/software/projects/LP/boards/3/backlog)

Each Jira card contains its work, completion checks and dependencies. Card IDs identify work items; their numbers do not determine execution order. Estimates are provisional story points, not recorded hours.

## Users and account access

- Proposed personas: customer, bakery owner and bakery employee. Their detailed needs still require research.
- Customers have their own accounts.
- Owners and employees use the shop-operator permission type. This release provides one operator account per shop; separate employee accounts are outside scope.
- A project administrator uses Django's built-in admin to review shop registration. New shops start Pending. Only Approved shops with complete profiles can publish.
- Approval is a manual onboarding control; it does not certify food safety or business legality.

## Core rules

- One website and one shared database serve customer and shop pages.
- Each reservation covers one food listing and a positive whole-number quantity.
- Prices use USD with decimal precision. Customers pay at pickup; there is no online payment or platform fee in this release.
- All permissions, prices, stock and time limits are checked on the server.
- Free stock = allocated stock - Confirmed units - Collected units. Collection never deducts free stock again.
- Customer cancellation is permitted strictly before pickup starts and releases units once when the offer is open.
- If the shop cancels reserved food because it is unavailable, those units are removed from allocated stock rather than returned to sale.
- Collection is allowed from pickup start until just before pickup end. Uncollected reservations expire after pickup ends.
- Store the agreed food, prices, address and pickup times with each reservation. Later profile changes must not rewrite those terms.
- Show times in Asia/Beirut using timezone-aware storage.

Search/filtering and collected-value summaries are Should features. Complete required flows and release checks first.

Delivery, online refunds, combined shopping carts, partial pickup, delegated-pickup workflows, ratings, chat and email/SMS alerts are outside the agreed release.

## Planned implementation

- Django with server-rendered templates and a small amount of JavaScript.
- PostgreSQL as the shared database.
- Django authentication and built-in administration.
- Logical apps: accounts, marketplace and reservations.
- Figma for connected screen designs.
- Automated checks and a shared HTTPS deployment with persistent database and photo storage.

Exact versions, installation commands and deployment configuration will be documented during [LP-6](https://alexdekermen05.atlassian.net/browse/LP-6). Do not assume the application can run from this repository yet.

## Contribution workflow

1. Choose a Jira card, read its completion checks and move it to In Progress when work begins.
2. Pull the latest main branch.
3. Create a branch named with the card ID and a short description, for example LP-7-shop-registration.
4. Implement a small, coherent change and run the relevant checks.
5. Commit with a clear message containing the Jira key.
6. Push the branch and open a pull request to main. Link the Jira card, explain the change and report actual validation.
7. Obtain review, resolve feedback and merge when checks and completion criteria pass.
8. Pull the updated main branch. Move the Jira card to Done only when its full completion checks pass and evidence is attached.

The first setup branch is LP-1-repository-setup. Its documentation pull request establishes the workflow; it does not prove that all LP-1 checks, including a second contributor's clone and review, are complete.

## Repository hygiene

The Python .gitignore excludes common temporary files, local virtual environments and .env files. Keep passwords, secret keys and database credentials out of commits. Add safe example configuration when the application setup exists.

Preserve database migrations and useful test evidence. Use fictional labelled demonstration data and communicate demo credentials privately.
