# LP-14 — Account permissions and input validation audit

Audit of every endpoint in `services/accounts` and `services/marketplace` and every page
in `web/frontend`, as of branch `LP-14-permissions-validation` (based on LP-13).

**Callers:** *anonymous* (no token or session), *customer* (Accounts role `CUSTOMER`),
*operator* (Accounts role `SHOP_OPERATOR`), *staff* (a local Django-admin account in the
service concerned; Accounts staff and Marketplace staff are separate accounts).

## How identity is established

| Service | Mechanism | Where |
| --- | --- | --- |
| Accounts | RS256 access token (simplejwt `JWTAuthentication`); default permission `IsAuthenticated` | `services/accounts/config/settings.py` `REST_FRAMEWORK`, `SIMPLE_JWT` |
| Marketplace | RS256 access token verified with the **public key only**; `exp`, `token_type`, `user_id` required, `token_type` must be `access`, algorithm pinned to RS256; default permission `IsAuthenticated`. **No session authentication on any API view** (sessions exist only for the Django admin). | `marketplace/authentication.py` `AccessTokenAuthentication.authenticate`; `config/settings.py` `REST_FRAMEWORK` |
| Web | Server-side DB session holding the access and refresh tokens; the browser only has the HttpOnly session cookie. A session is "signed in" only with **both** a refresh token and an account record. Only `SHOP_OPERATOR` accounts get a session. CSRF middleware on. | `frontend/account_views.py` `signed_in`, `operator_required`, `sign_in`; `frontend/tokens.py` |

Ownership in Marketplace always comes from the token: the caller's shop is
`Shop.objects.filter(operator_user_id=request.user.id)`. No endpoint accepts a shop ID.

## Accounts (`services/accounts`, port 8001)

| Endpoint | Allowed callers | Ownership rule | Enforced in |
| --- | --- | --- | --- |
| `POST /api/auth/register/` | anonymous | Always creates a `SHOP_OPERATOR`; `role`, `is_staff`, `is_superuser`, any unknown field → 400. Email normalised to lower case, unique case-insensitively (API check + DB constraint `accounts_user_email_ci_unique`). Django password validators. Throttle 10/hour per client. | `accounts/views.py` `ShopOperatorRegisterView`; `accounts/serializers.py` `ShopOperatorRegistrationSerializer.to_internal_value`, `validate_email`, `validate`; `accounts/models.py` `User.Meta.constraints` |
| `POST /api/auth/token/` | anonymous | Email + password; one generic error for all failures. Throttles 30/min per client and 5/min per email. | `accounts/views.py` `SignInView`; `accounts/serializers.py` `SignInSerializer`; `accounts/throttles.py` `SignInEmailThrottle` |
| `POST /api/auth/token/refresh/` | holder of a valid, non-blacklisted refresh token | — | simplejwt `TokenRefreshView` |
| `POST /api/auth/logout/` | holder of a refresh token | Blacklists that refresh token. Access tokens stay valid until they expire (15 min). | simplejwt `TokenBlacklistView` |
| `GET /api/users/me/` | any signed-in user (customer or operator) | Returns only the caller's own record; read-only (other methods → 405) | `accounts/views.py` `MeView`; default `IsAuthenticated` |
| `/admin/` | staff (Accounts admin accounts) | No models registered; login/logout/index only. Bearer tokens give no access. | Django admin (`config/urls.py`) |
| `GET /health/live/`, `/health/ready/` | anyone | — | `config/health.py` |

## Marketplace (`services/marketplace`, port 8002)

| Endpoint | Allowed callers | Ownership rule | Enforced in |
| --- | --- | --- | --- |
| `POST /api/shops/` | operator | Creates the caller's shop (one per operator); only `name` accepted; repeat returns the existing shop unchanged | `views.py` `ShopCreateView` (`IsAuthenticated`, `IsShopOperator`); `serializers.py` `ShopCreateSerializer` (`RejectUnknownFieldsMixin`); `models.py` `Shop.operator_user_id` unique |
| `GET /api/shops/me/` | operator | Caller's own shop only | `views.py` `MyShopView.get_own_shop` |
| `PATCH /api/shops/me/` | operator | Caller's own shop; only `name`, `address`, `area`, `public_contact`; `approval_status`, `rejection_reason`, `reviewed_by`, `reviewed_at`, `operator_user_id` → 400; saves only edited fields | `views.py` `MyShopView.patch`; `serializers.py` `ShopProfileSerializer`, `validate_public_contact` |
| `GET /api/listings` | anyone (auth ignored) | Only offers that are PUBLISHED, from an APPROVED shop, pickup not ended, free stock > 0; no operator identity | `views.py` `browse_listings` (`authentication_classes([])`, `AllowAny`); `browse.py` `visible_listings`; `serializers.py` `PublicListingSerializer` |
| `POST /api/listings` | operator with a shop | Draft is created in the caller's shop, status DRAFT; `shop_id`/`status` → 400; **other unknown/read-only fields → 400 (LP-14 fix)** | `views.py` `create_listing`; `listing_service.py` `save_listing_draft`; `serializers.py` `ListingSerializer.to_internal_value` |
| `GET /api/listings/{id}` | anyone (auth ignored) | Same visibility as the list; everything else → 404 | `views.py` `listing_offer`; `browse.py` `visible_listing` |
| `PATCH /api/listings/{id}` | operator | Own shop's **draft** only (else 404); protected fields (`shop_id`, `status`, `id`, `terms_revision`, `first_reserved_at`, `created_at`, `updated_at`, `photo`) → 400; **unknown fields → 400 (LP-14 fix)** | `views.py` `edit_listing`; `serializers.py` `ListingSerializer` |
| CSRF-exempt dispatcher for `/api/listings` and `/api/listings/{id}` | — | `GET` → public view; anything else → operator view (which only allows POST / PATCH; others → 405). Safe because no API view accepts session cookies: a cookie-only write gets 401. | `views.py` `listings_collection`, `listing_resource` |
| `GET /api/listings/mine` | operator | Own shop's listings (drafts and published) | `views.py` `my_listings` |
| `POST /api/listings/{id}/publish` | operator | Own listing (else 403); shop APPROVED and profile complete, read from the DB; listing must be a valid draft; any request field → 400 | `views.py` `publish_listing`; `listing_service.py` `publish_listing`, `validate_listing_for_publication` |
| `POST /api/listings/{id}/photo` | operator | Own shop's **draft** only (else 404); exactly one `photo` field; JPEG/PNG by content, ≤ 5 MB; stored under a random name | `views.py` `upload_listing_photo`; `photo_service.py` `replace_listing_photo`; `image_validators.py` `validate_listing_photo`; `upload_paths.py` |
| `POST /api/listings/{id}/stock-adjustments` (LP-12) | operator | Own PUBLISHED listing (else 403/409), pickup not ended; only `quantity_change` and `reason`; actor, kind and listing come from the server; row locked | `views.py` `listing_stock_adjustments`; `serializers.py` `StockAdjustmentRequestSerializer`; `stock_service.py` `adjust_stock` |
| `GET /api/listings/{id}/stock-adjustments` (LP-12) | operator | Own listing only (else 403) | `views.py` `listing_stock_adjustments` |
| `/admin/` → Shop review (LP-9) | staff with `marketplace.review_shop` (change) or view permission (read) | Only the decision (`approval_status`, `rejection_reason`) is editable; reviewer and time set by the server; no add/delete. Operator tokens give no access. | `admin.py` `ShopAdmin.has_*_permission`, `save_model`; `ShopReviewForm.clean` |
| `GET /health/live/`, `/health/ready/` | anyone | — | `config/health.py` |

## Reservations (`services/reservations`, port 8003)

Only `GET /health/live/` and `/health/ready/` (anyone). No business endpoints yet.

## Web frontend (`web`, port 8000)

| Page | Allowed callers | Ownership rule | Enforced in |
| --- | --- | --- | --- |
| `/` status page | anyone | — | `views.py` `home` |
| `/offers/`, `/offers/{id}/` (LP-13) | anyone | Shows only what the public Marketplace API returns | `browse_views.py` `browse_offers`, `offer_detail` |
| `/signup/` (LP-56) | anonymous (signed-in → their landing page) | Creates a customer or restaurant account; any other `account_type` is rejected before Accounts is called, and no role field is ever forwarded | `account_views.py` `signup`; `forms.py` `SignUpForm` |
| `/login/` (LP-56) | anonymous | One page for every account. Customers land on `/`, operators on `/workspace/`, staff on the Django admin with no web session kept; `next` must be same-host | `account_views.py` `login`, `landing_url` |
| `/sign-in/`, `/register/` | anyone | Redirect to `/login/` and `/signup/`, keeping the query string | `config/urls.py` |
| `/account/` (LP-56) | customer | The customer's own name and email | `account_views.py` `my_account` (`customer_required`) |
| `POST /sign-out/` | anyone (POST + CSRF only) | Revokes and deletes the session's tokens | `account_views.py` `sign_out` |
| `/workspace/` | operator | Shop and listings from Marketplace using the session's token | `account_views.py` `workspace` (`operator_required`) |
| `/workspace/finish-setup/` | operator | Creates the caller's shop | `account_views.py` `finish_setup` |
| `/workspace/profile/` | operator | Edits the caller's shop; only the four profile fields are forwarded | `account_views.py` `shop_profile`; `forms.py` `ShopProfileForm` |
| `/workspace/listings/new/` | operator | Only `LISTING_FIELDS` are forwarded | `listing_views.py` `create_listing` |
| `/workspace/listings/{id}/edit/` | operator | Listing must be in the caller's `listings/mine` and DRAFT, else 404; only `LISTING_FIELDS` forwarded | `listing_views.py` `edit_listing` |
| `POST /workspace/listings/{id}/publish/` | operator (POST only) | Marketplace checks ownership and approval | `listing_views.py` `publish_listing` |
| `POST /workspace/listings/{id}/stock/` (LP-12) | operator (POST only) | Only the change and reason are forwarded | `listing_views.py` `adjust_stock` |
| `/health/live/`, `/health/ready/` | anyone | — | `health.py` |

Signed-out visitors are redirected to `/login/?next=…` from every operator or customer page
before any service is called. A signed-in account with the wrong role is sent to its own
landing page, also before any service is called.

## Gaps found and fixed

Each fix has a test that failed before the fix and passes after it.

| # | Gap | Fix | Test(s) that failed before the fix |
| --- | --- | --- | --- |
| 1 | `POST /api/listings` and `PATCH /api/listings/{id}` silently ignored unknown and read-only fields (`terms_revision`, `first_reserved_at`, `id`, `free_quantity`, `photo_display`, `created_at`, typos such as `colour`) and returned 201/200. Nothing was stored, but forged or mistyped input was not reported. | `ListingSerializer.to_internal_value` rejects every field that is not writable with "This field is not allowed." (same message as the other Marketplace serializers). `services/marketplace/marketplace/serializers.py` | `test_lp14_permissions.ProtectedFieldTests.test_listing_create_rejects_protected_fields`; `test_lp14_validation.UnknownFieldTests.test_listing_create_rejects_unknown_and_read_only_fields`, `test_listing_edit_rejects_unknown_fields` |
| 2 | Sign-in trimmed spaces from the password but registration kept them, so an account registered with a leading/trailing space (allowed by the API and the web form) could never sign in. | `SignInSerializer.__init__` sets `trim_whitespace = False` on the password field. `services/accounts/accounts/serializers.py` | `accounts.test_lp14.PasswordValidationTests.test_password_is_not_trimmed` |
| 3 | The "not similar to your name" password rule did not check `display_name` (Django's default only checks username/first/last name/email), although the registration page promises it. | `UserAttributeSimilarityValidator` option `user_attributes = ('email', 'display_name', 'first_name', 'last_name')`. `services/accounts/config/settings.py` | `accounts.test_lp14.PasswordValidationTests.test_password_rules` [like the display name] |

Existing test changed: `marketplace.test_serializers.ListingSerializerTests.test_cannot_set_published_status`
asserted the old "silently drop `status`" behaviour; it now asserts that `status` is rejected.

## Checked and already correct (no change)

- **Anonymous** gets 401 on all 10 protected Marketplace requests, 401 on `/api/users/me/`, and a sign-in redirect from all 11 protected web requests with no service call.
- **Wrong role:** `CUSTOMER`, empty, `ADMIN` and lower-case roles get 403 on every operator API. An `is_staff` token claim grants nothing. Operator tokens don't sign in to either admin. Customers can't get a web session.
- **Operator A vs B:** A can't edit, publish, upload a photo to, read the stock history of, or adjust the stock of B's listings. A only ever sees their own shop and listings, and nothing of B's is changed or leaked in error bodies.
- **Protected fields** `status`, `shop_id`, `operator_user_id`, `terms_revision`, `first_reserved_at`, `approval_status`, `reviewed_by` (and `reviewed_at`, `rejection_reason`, `actor_user_id`, `kind`) are refused by every Marketplace write. The web pages never forward them.
- **JWTs:** garbage, empty, expired, tampered payload, signed by another RSA key, `alg: none`, HS256 signed with the public key, refresh-as-access, zero or non-numeric `user_id`, and a deactivated user (Accounts) are all rejected. Public pages ignore bad tokens and still hide drafts.
- **CSRF-exempt dispatcher:** a signed-in Marketplace admin session posting JSON or a form to `/api/listings`, or PATCHing `/api/listings/{id}`, gets 401 and writes nothing. PUT/DELETE get 405. No URL-routed API view has `SessionAuthentication` (structural test). Web forms all require a CSRF token.
- **Validation:**
  - Prices must be > 0, the discount must be below the original (also checked against saved values on edit), at most 2 decimals, at most 8 integer digits, and NaN, Infinity and non-numeric values are refused.
  - Quantities must be non-negative whole numbers up to 2,147,483,647 (huge values get 400, not 500), and publishing needs stock above 0.
  - Pickup must start before it ends, and publishing needs an end in the future. Times without an offset are read as Asia/Beirut (the web form sends `datetime-local`) and always stored timezone-aware.
  - Phone numbers allow 7–15 digits from `+ - ( ) . space` and no single-repeated digit.
  - Emails must be well-formed, at most 254 characters, and unique case-insensitively in both the API and the database.
  - Passwords: Django's minimum length, common-password, numeric-only and similarity checks, at most 128 characters.
  - Photos are checked by content as JPEG/PNG. Fake extensions, GIF/BMP, truncated, empty and over-5 MB files are refused, there must be exactly one `photo` field, and files are stored under random names.
  - Stock-adjustment reasons are 1–500 characters after trimming. Zero, non-integer and huge changes are refused.

## Observations, not changed

These are not defects under the current rules. They are listed for the team to decide on.

- A real JPEG uploaded as `x.png` is stored with a `.png` name (the extension comes from the uploaded name, limited to jpg/png). The content is always a verified JPEG or PNG, so this isn't a safety issue.
- For a listing the caller doesn't own, publish and stock endpoints answer 403 while edit and photo endpoints answer 404. Both refuse, but 403 confirms that the listing ID exists.
- Drafts may hold a pickup end in the past. The future-end rule applies when publishing and when adjusting stock.
- An access token stays valid for up to 15 minutes after sign-out or a role change (documented LP-7 trade-off).
- The simplejwt sign-in, refresh and logout endpoints ignore extra fields. They store no data.

## Tests added

| Service | File | Tests |
| --- | --- | --- |
| Marketplace | `marketplace/test_lp14_permissions.py` | 17 |
| Marketplace | `marketplace/test_lp14_validation.py` | 26 |
| Accounts | `accounts/test_lp14.py` | 9 |
| Web | `frontend/test_lp14_permissions.py` | 9 |
