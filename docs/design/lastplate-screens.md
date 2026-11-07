# LastPlate screens: customer and restaurant (from LP-16)

Short text version of the design canvas, so Claude Code can read it cheaply.
Phone first (390 px wide). On a laptop the page sits in a 430 px centred column.
Admin screens are not here (admin uses /console, LP-57).

## Look (already in `web/static/css/lastplate.css`, LP-61)

| Token | Value | Used for |
|---|---|---|
| background | #FFF5EF | page |
| card | #FFFFFF, 1px #F5DCCD border, 22px corners | cards, sections |
| text / secondary | #3A2016 / #6E5548 | all text |
| button | #B8410F, hover #8F3209, white text, pill (999px), min-height 52px | main actions |
| outline button | white, 1px #E9CDBB (or #B8410F), pill | second actions |
| accent | #F2652A | logo inner ring, LastBag card border |
| tint | #FFEDE2 bg + #8F3209 text | Today tag, status pills, info notes |
| discount | white text on #DC2626 pill | "-56%" badge |
| danger | #B42318 | Reject, Cancel, Log out text |
| restaurant header | #3A2016 bg, white text | every restaurant screen |
| inputs | 48–52px high, 12–14px corners, 1px #E9CDBB | forms |

- Font: Nunito everywhere (400–900). Headings 800–900.
- Logo: CSS ring (outer #3A2016, inner #F2652A) + "LastPlate" Nunito 900 #3A2016. On the dark restaurant header the outer ring is white.
- Real food photos on every offer, restaurant cover and item (object-fit: cover).
- Touch targets ≥ 44px. Text contrast ≥ 4.5:1. Icons are inline stroke SVGs, no emoji.

## Shared parts

- **Offer card:** photo on top with red "-56%" badge (top left) and white "N left" chip (top right) → SHOP · AREA in small capitals → bold title → "Pickup [Today] 19:00–20:00" → "Allergens …" → divider → new price (bold) + old price struck through + "View offer" pill.
- **Customer bottom bar (4):** Home · Search · My orders · Profile. Active item #B8410F, others #6E5548.
- **Restaurant bottom bar (5):** Today · Orders · Offers · My restaurant · Log out.
- **Cart bar (LP-62):** fixed at the bottom of a restaurant page once something is added: "3 items · $12.50 · View cart →", tangerine pill.

## Start (everyone)

**0 · Log in** `/login` (LP-56, done)
Logo + "Great food, rescued before closing time." → Email, Password, "Keep me logged in (30 days)" → Log in. Note: customer lands on Home, restaurant on Today. Bottom: "New to LastPlate? Create an account".

**0b · Sign up** `/signup` (LP-56, done)
Back arrow + "Create your account". "How will you use LastPlate?" two big cards: **I want to buy food** / **I own a restaurant** (selected = #B8410F border, #FFEDE2 bg). Name (label "Owner name" for restaurant), Email, Password. Restaurant shows note "Next: your restaurant's details, location and photos. LastPlate reviews every restaurant before it goes live." Button: "Create account" (customer → Location) or "Continue to restaurant details" (restaurant → Apply). Admin is never an option.

## Customer path

**1 · Home** `/` (LP-39 browsing, LP-18 bottom bar)
White header: logo + location pill "Hamra · current location" (→ 1b). Search field (pill, #FBE7DA). Category chips: All (dark, selected) · Bakery · Café · Sushi · Pastry. Title "Near you, still available today" + "12 places". Restaurant cards: cover photo with red "-65% · 3 LastBags left" badge → name + distance → "Bakery · Pickup today 19:00–20:00" → "~~$15~~ from $5". Customer bottom bar.

**1b · Choose location** (LP-51, Sprint 3 extra)
"Where should we look for food?" → "Use my current location" button → "or enter it yourself" → Area or address field + map → Saved places (Home · Hadath, University · Hamra) with "Use" → "Save this location".

**2 · Restaurant page** (LP-39 + LP-62 cart)
Cover photo with back button → white section: name, "Bakery · Hamra, Bliss Street · 0.6 km", phone link.
LastBag card (2px #B8410F border): "LastBag · surprise" pill + "3 left" → **sneak peek** (photo blurred, one sharp circle "spark", label "Sneak peek · tap to spark", LP-54) → "Today's bakery surprise bag", "Usually: croissants, man'oushe, a loaf of bread", "May contain: gluten, milk, sesame" → "Worth ~~$15~~ · **$5**" → **Add**.
"Specific items": rows with 72px photo, name, "4 left · Pickup 19:00–20:00", "~~$9~~ **$4**", **Add**. After adding, Add becomes − qty +, and the cart bar appears.

**3 · Cart / checkout** (LP-62 cart, LP-19 checkout)
Header "Your order". Restaurant name + lines (name, − qty +, line price, remove). One restaurant per cart: adding from another asks "Start a new cart?". Pickup today 19:00–20:00 · Where · Payment "Cash at pickup" ("Card payment is coming in a later release"). Note in tint: "The restaurant confirms your order. You can cancel until they accept it." Footer: "Total to pay at pickup $X" + **Place order**. Not signed in → sign in and come straight back.

**4 · Order status + pickup code** (LP-22)
Header "Order placed". Big #B8410F card: "Show this code at pickup" + code **K7Q2MX** + "2 × LastBag · $10.00 cash at pickup". Status timeline: Placed (filled) → Accepted ("The restaurant has up to 10 min to answer") → Ready for pickup → Collected. Restaurant card: name, address + pickup time, "Call …". Footer: "My orders" + "Cancel order" (red outline, LP-27).

**5 · My orders** (LP-27)
"Current": card with restaurant · items, time · price, status pill (Placed, tint). "Past": Collected (neutral pill), Rejected (red pill, shows reason e.g. "Sold out, sorry"). Customer bottom bar.

**6 · Profile** (LP-18)
Avatar initials + name + email. Impact card (tint): "23 plates saved · $142 saved › See your impact" (LP-52). Settings card: My location (Change), "Use my current location" switch, "Stay signed in · 30 days". **Log out** (red text, outline). Customer bottom bar.

**6b · Your impact** (LP-52, extra)
Big #B8410F card "You've saved 23 plates from going to waste since September". Two small cards: "$142 saved vs full price" (red), "≈ 58 kg CO₂ avoided (estimate)". Dark card: "LastPlate community this month: 1,240 plates saved in Beirut".

## Restaurant path

**7 · Register your restaurant** (LP-37)
Back → "Register your restaurant", 3-part progress bar, "Step N of 3 · name".
Step 1 Restaurant info: name, category (Bakery, Café, Restaurant, Sushi, Pastry, Grocery, Other), phone.
Step 2 Location: map "drag the pin to your door" + written address.
Step 3 Photos and conditions: + Add cover photo, + Logo, ☐ "I accept LastPlate's conditions for restaurants".
Footer: Back + Continue → last step "Send request".

**8 · Request pending** (LP-37 / LP-23 banner)
Clock icon, "Request sent", "The LastPlate team is reviewing Cedar Bakery. We'll notify you as soon as you're approved." "What happens next" checklist: Request sent · conditions accepted ✓ → LastPlate reviews → Approved: log in → Post your first LastBag. Log out button.

**9 · Today** `/business` (LP-23 + LP-28)
Dark header "Restaurant mode · Cedar Bakery · Today" + "1 new" pill. "New orders": card with #B8410F border, "2 × LastBag · $10 cash", "8 min left", "Rami K. · pickup 19:00–20:00 · code K7Q2MX", **Accept** / **Reject** (red text). "Today's offers": LastBag row with − count + ("$5 · 2 reserved · 3 left for customers"), item row with Live switch ("visible to customers" / "hidden (off)"). Buttons: **+ New offer**, **↻ Repost yesterday's** (LP-44). Restaurant bottom bar.

**10 · Handle an order** (LP-28 / LP-24)
Dark header "Order K7Q2MX". Summary: "2 × LastBag", status pill, "Rami K. · pickup today 19:00–20:00", "$10.00 cash at pickup".
Accepted → "Pack the order…" + **Mark ready**. Ready → pickup code field + ☑ "Cash received: $10.00" + **Confirm handover**. Collected → check icon, "2 bags saved from the bin", "Back to Today".

**11 · New offer** (LP-38)
Dark header "New offer". Toggle: **LastBag (surprise)** / **Specific item**.
LastBag: What's usually inside, Worth ($), Your price ($), "+ Photo of today's bag (we blur it and reveal one random spark)", May contain (allergens).
Item: Item name, Original ($), Discounted ($), Ingredients and allergens.
Both: Quantity, Pickup time, + Add photo. Footer: Save draft / **Publish now**.

**12 · My restaurant page (edit / preview)** (LP-59)
Dark bar "My restaurant page · editing" with a Preview switch. Cover photo ("Change cover" in edit mode), name/category/phone with edit button, LastBag and item cards with edit ✎ / remove ✕, "+ Add an offer". Preview on = exactly what customers see.
