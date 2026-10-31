# LP-3: Project rules and team responsibilities

LastPlate · Sprint 1 · Prepared 31 October 2026 · Evidence for Jira card LP-3

## 1. Agreed decisions

- Sprint 1 goal: an approved shop publishes food and manages stock, and visitors browse and inspect offers.
- Reservations are Sprint 2. Cancellation of active reservations is Sprint 3.
- Prices are in USD. Customers pay in cash at pickup. There is no online payment, commission or platform fee in this release.
- Times are displayed in the user's local time (Lebanon timezone) using timezone-aware storage.
- Required listing fields: food name, original price, discounted price, quantity, pickup address, pickup times and ingredients. Photo is optional.
- Shops start Pending and the administrator approves them in Django admin. Only Approved shops with complete profiles can publish. Approval is a manual onboarding check; it does not certify food safety or business legality.
- Stock rule: free stock = allocated stock − Confirmed − Collected.
- One operator account per shop, shared by its owner and employees. Customers have their own accounts.
- Personas: customer, bakery owner and bakery employee (proposed; see the research gaps below).
- Out of scope: delivery, online payment and refunds, separate employee accounts, maps, chat, and email or SMS alerts.
- Workflow: one shared GitHub repository, a branch and pull request per Jira card with the card key in the name, a teammate review before merging, and Done only when the completion check passes and evidence is attached.

## 2. Research evidence, gaps and follow-up owners

**Evidence so far.** Five interviews with LastPlate team members (Carla, Jad, Alex, Omar and Nizam), five artifacts (an Instagram discount story and photos of a paper reservation notebook), and the LastPlate Features Document (personas, features, requirements, use cases and tests). All user data is second-hand: it is what the team reported that customers and restaurants said.

**Assumptions.**

- Shops know their surplus only about one hour before closing, so posting an offer must be fast.
- Customers use a phone, search by area and pay in cash.

**Gaps and unanswered questions.** Owners are proposed from the team plan; the team confirms them.

| Gap or question | Type | Proposed owner | Due |
| --- | --- | --- | --- |
| Direct interviews with 3 restaurants to check the main facts | Gap | Team 2 | Before Sprint 2 starts (5 Nov) |
| Direct interviews with 5 customers | Gap | Team 1 | Before Sprint 2 starts (5 Nov) |
| Older customers and counter staff (thin personas) | Gap | Team 3 | Before Sprint 3 starts (19 Nov) |
| Customer cancellation cut-off: before pickup starts (README) or before pickup ends (features document) | Question | Team 1 | Before Sprint 3 starts (19 Nov) |
| Collection after the pickup end, and No-show handling | Question | Team 2 | Before Sprint 2 starts (5 Nov) |
| Ratings and search filters: out of this release or a Should feature | Question | Alex (Product Owner) | Before Sprint 2 starts (5 Nov) |
| Reservation status names: Confirmed (README) vs Pending (features document) | Question | Alex (Product Owner) | Before Sprint 2 starts (5 Nov) |
| Restaurants fear customers will wait for discounts | Question | Team 2 | Before Sprint 3 starts (19 Nov) |
| Confirm the core rules with the professor before coding | Action | Alex (Product Owner) | This week |
| Align the plan's payment tasks (plan cards 8, 11 and 14) with cash at pickup | Action | Jad Tannir (Scrum Master) | Before attaching the plan |

## 3. Team roles, card owners, estimates and dates

**Roles**

| Member | Team | Role |
| --- | --- | --- |
| Alex Dekermenjian | Team 1 | Product Owner and Developer |
| Carla Jaffal | Team 1 | Developer |
| Jad Tannir | Team 2 | Scrum Master and Developer |
| Nizam | Team 2 | Developer |
| Jad Moharam | Team 3 | Developer |
| Omar Tabsh | Team 3 | Developer |
| To be named by the team |  | Administrator (approves shops in Django admin; a supporting role) |

The Product Owner and Scrum Master are coordination roles. All six members develop and test within their teams, and each team reviews another team's work.

**Sprint dates (two weeks each)**

| Sprint | Dates | Working result |
| --- | --- | --- |
| 1: Publish and browse | 22 Oct to 5 Nov 2026 | Staff can publish an offer; customers can sign in and view it. |
| 2: Reserve and collect | 5 Nov to 19 Nov 2026 | Customers reserve food; the shop confirms payment and collection once. |
| 3: Cancellations and release | 19 Nov to 3 Dec 2026 | Cancellation and recovery flows, tests, final demonstration and report. |

**Sprint 1 cards, owners and estimates** (story points are provisional and relative, not hours)

| Card | Task | Points | Status | Jira assignee | Lead team (plan) |
| --- | --- | --- | --- | --- | --- |
| LP-1 | Set up the shared GitHub repository | 2 | Done | Alex Dekermendjian | Team 3 |
| LP-3 | Confirm project rules and assign team responsibilities | 2 | In Progress | Jad Tannir | Team 2 |
| LP-4 | Design the Sprint 1 screens and navigation in Figma | 3 | To Do | To assign | To confirm |
| LP-5 | Design the database tables and relationships | 3 | Done | None recorded | Team 3 |
| LP-6 | Set up Django, PostgreSQL and project configuration | 5 | Done | Alex Dekermendjian | Team 3 |
| LP-7 | Register and sign in as a shop operator | 5 | To Do | To assign | Team 3 |
| LP-8 | Create and update the shop profile | 3 | To Do | Carla Jaffal | Team 3 (check) |
| LP-9 | Approve or reject shop accounts as an administrator | 3 | To Do | Carla Jaffal | Team 3 (check) |
| LP-10 | Create, save drafts and publish food offers | 5 | To Do | To assign | Team 2 |
| LP-11 | Implement listing photos and ingredient information | 3 | To Do | To assign | Team 2 |
| LP-12 | Manage shop offers and adjust available stock | 5 | To Do | To assign | Team 2 |
| LP-13 | Browse available food and view offer details | 5 | To Do | To assign | Team 1 |
| LP-14 | Verify account permissions and input validation | 3 | To Do | To assign | Team 3 |
| LP-15 | Integrate, test and deploy the Sprint 1 demonstration | 5 | To Do | To assign | All teams |

Sprint 1 totals 52 points: 10 are Done, 2 are In Progress and 40 are To Do. Capacity check: the team compares the remaining 42 points with its real availability before 5 Nov and moves the lowest-priority cards to Sprint 2 if the sprint is too large. Cards with no assignee are assigned in Jira before work starts on them.

The Jira cards are the master record. The team plan maps each of its tasks to the LP cards in its task table.

## 4. Sprint 1 demonstration

What we will show at the sprint review:

1. **Approval.** The administrator opens Django admin and approves a pending shop (LP-9). Sign-in and the shop profile (LP-7, LP-8) come first.
2. **Publishing.** The approved shop publishes a listing with price, quantity, pickup times and ingredients (LP-10, LP-11).
3. **Stock.** The shop updates its available stock (LP-12).
4. **Browsing.** A visitor browses the offers and opens one to see its details (LP-13).

The teams hand over in order: Team 3 presents sign-in and approval, Team 2 presents publishing and stock, and Team 1 presents browsing. Each pair can explain its step and how it connects to the next. The whole team rehearses the demonstration once before the review, and LP-15 covers integration, testing and deployment.

## Evidence attached to this card

- This document
- The team plan with the LP card mapping
- The LastPlate Features Document
- Repository and workflow evidence from LP-1
