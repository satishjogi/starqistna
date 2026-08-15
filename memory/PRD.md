# Transit E1 — Bus Booking System (PRD)

## Problem statement
Build a complete online bus booking system where visitors can search departure terminals & arrival terminals (some with sub-terminals). Guest checkout is supported, with a preferred flow for registration & login so users see past/upcoming bookings. Must expose an API for future mobile (iOS/Android). Payments via iPay88 & Stripe. Seat selection must include double-booking protection. Two passenger categories (adult & child).

## Architecture (decided with user — Option C)
- Web: React (CRA)
- Backend: FastAPI (`/api/*`) on a single MongoDB database
- Same backend will serve a future React Native mobile app (shared DB, shared APIs)
- JWT auth + guest checkout
- Stripe for MVP; iPay88 deferred until merchant credentials shared

## Core requirements
- Search: from/to terminal (grouped by city with sub-terminals), date, adult+child count
- Schedules listing with operator, times, seats available, fare
- Seat map: 10 rows × 4 seats (2+aisle+2), available / selected / booked
- Double-booking protection: MongoDB **partial unique index** on `seat_locks(schedule_id, seat_number)` for status ∈ {locked, booked}. Locks TTL 10 minutes, cleaned on race.
- Categories: adult & child. Child = 50% of adult. Pricing computed server-side only.
- Guest + authenticated bookings. Bookings attached to `user_id` when logged in.
- Payment via Stripe Checkout (redirect). Poll status + webhook idempotent finalize.
- User dashboard: upcoming + past bookings with reference codes.
- Admin panel: stats + bookings list + schedule CRUD.

## User personas
- **Traveler (guest)** — quick one-off booking, enters contact email/phone.
- **Traveler (registered)** — sees past/upcoming bookings.
- **Admin** — manages terminals, schedules, reviews bookings.
- **Future mobile app** — consumes the same `/api/*` endpoints.

## Implemented (2026-04-17)
- **Backend (`/app/backend/server.py`)**
  - Collections: users, terminals, schedules, bookings, seat_locks, payment_transactions
  - Endpoints: `/api/auth/{register,login,me}`, `/api/terminals`, `/api/search`, `/api/schedules/{id}`, `/api/seats/{lock,release}`, `/api/bookings` (create/me/get), `/api/payments/{checkout,status,webhook}`, `/api/admin/{stats,bookings,schedules}` (GET/POST)
  - Seed on startup: 14 Malaysian/SG terminals, 560 schedules (10 routes × 14 days × 4 times), admin user
  - Unique indexes: `users.email`, `bookings.reference`, `payment_transactions.session_id`, partial unique on `seat_locks(schedule_id, seat_number)` for active statuses
- **Frontend (`/app/frontend/src`)**
  - Pages: Home, SearchResults, SeatSelection, Passengers, PaymentCallback, PaymentCancel, Login, Register, Dashboard, BookingDetail, Admin
  - Design system: Swiss/High-Contrast — Klein Blue (#002FA7), Signal Orange (#FF4500), Cabinet Grotesk + Manrope + JetBrains Mono
  - Auth context with localStorage JWT, axios interceptor
  - Session booking-store for flow continuity through payment redirect
- **Testing**: 22/22 backend tests pass, all frontend flows verified (iteration_1.json)

## Implemented (2026-04-17 · iteration 2)
- **Promo codes**
  - `promo_codes` collection (`code` unique, `type: percent|flat`, `value`, `currency`, `max_uses`, `used_count`, `valid_until`, `active`)
  - Endpoints: `POST /api/promo/validate`, admin `GET/POST/PATCH/DELETE /api/admin/promo-codes`
  - Applied server-side in `POST /api/bookings` via `_calc_pricing(promo)` — discount is NEVER trusted from frontend
  - `used_count` auto-increments on booking confirmation via `_finalize_booking` (idempotent, called from both status poll & Stripe webhook)
  - Seeded codes: `WELCOME10` (10%), `RAYA5` (flat RM 5)
  - Frontend: Passengers page promo input with Apply/Remove, discount row in sidebar; Admin tab "Promo codes" with create form + toggle/delete
- **QR codes**
  - `qrcode.react` (QRCodeSVG) renders plain-text QR of `booking.reference` on `/payment/success` and `/bookings/:id` (status=confirmed)
  - `POST /api/boarding/validate` for existing gate readers — accepts `{reference, gate?, mark_boarded?}` → returns passenger+seat info; idempotent (returns `already_boarded:true` on repeat); writes `boarded_at` + `boarded_gate`
  - BookingDetail shows "Boarded" badge once `boarded_at` is set
- **Round-trip booking (upsell)**
  - `ReturnUpsell` section on PaymentCallback success page — reuses outbound adult/child split, defaults return date to outbound date, reverses from/to terminal IDs when navigating to `/search`

## Implemented (2026-04-19 · iteration 3)
- **Passenger counter UI polish** — `−`, number, `+` on Home page are now tightly grouped & centered (desktop + mobile)
- **Google social login (Emergent-managed)** — `POST /api/auth/google/session`, auto-link by email, 2FA still enforced; `GoogleAuthButton` on `/login` and `/register` + `/auth/callback` page.
- **My Next Trip widget** on Dashboard — countdown, QR, "View ticket" + "Add to calendar" (.ics) CTAs.
- **Tighter horizontal gutters** — `px-6 md:px-12 lg:px-20` → `px-4 md:px-6 lg:px-10` site-wide.
- **Stripe webhook signature verification** — `STRIPE_WEBHOOK_SECRET` env flag; when set the webhook rejects unsigned / bad-signature requests.
- **Admin Payments tab** — `GET /api/admin/payments` with status filter + aggregate summary; Admin UI has summary cards, filter pills, transaction table with Stripe Dashboard deep-links.
- **Payment methods expanded + iPay88 removed** — all via single Stripe account. MYR: Card / GrabPay / FPX. SGD: Card / GrabPay. Backend passes `payment_methods=[body.gateway]` to Stripe Checkout; `payment_method` stored on each txn. GrabPay + FPX require one-time activation in Stripe Dashboard → Settings → Payment methods before live use.
- **"Popular right now" live section** on Home page — `GET /api/popular/now` returns soonest upcoming schedule per popular city pair (KL→Melaka, KL→JB, KL→Penang, KL→SG, PEN→JB, SG→KL, JB→KL, PEN→KL). Frontend grid cards show "Next bus to …", "departs in Xh Ym · reach by HH:MM", fare + seats left; "LEAVES SOON" badge when <2h; click deep-links to `/search` with terminals + date pre-filled.
- **Live pulsing countdown + urgency tiers + scarcity flash + tighter hero** (2026-04-20)
  - Countdown ticks every second based on actual `departure_date + departure_time`
  - `pulse-urgent` keyframes: ≤60min = "LEAVES SOON" pulsing red, ≤10min = "BOARDING NOW"
  - 🔥 "Only N seats left" flash on any card with 1–3 seats remaining
  - Auto-refresh every 2 minutes (silent)
  - Hero padding `pt-16 pb-24` → `pt-12 pb-8` so Popular Now is visible above the fold on 1400×900
  - **Route-time diversification migration**: seed previously used identical 4 time slots for every route so all countdowns were identical. New `_diversify_popular_schedules()` migration adds route-specific varied times (07:15/10:30/13:45/17:20/19:00 for KL→Melaka, etc.) for next 14 days — idempotent, preserves existing schedules & bookings.
- **Testing**: iterations 3–9 all green.

## Implemented (2026-04-20)
- **Passenger form validation** — name fields enforce letters/spaces/hyphens/apostrophes/dots only (blocks digits & symbols on keystroke); phone enforces E.164 format (`+` + country code + 7–15 digits) with `type="tel"` for mobile numeric keypad, pattern check, and friendly error message.
- **Admin audit log**
  - New `audit_logs` collection (`actor_id`, `actor_email`, `action`, `resource`, `resource_id`, `details`, `ip`, `created_at`); indexed on `created_at` and `(resource, action)`.
  - `log_audit()` helper writes entries without ever breaking the parent request (warnings-only failure mode).
  - Instrumented endpoints: `POST/PATCH/DELETE /admin/promo-codes`, `POST/PATCH/DELETE /admin/terminals`, `POST /admin/schedules`, `POST /admin/schedules/bulk`, `DELETE /admin/schedules/range`.
  - Captures real client IP via `X-Forwarded-For` fallback to `request.client.host`.
  - `GET /api/admin/audit-logs` supports `resource`, `action`, `actor_email` filters + `limit` (max 1000).
  - Admin UI: new "Audit log" tab with Resource / Action / Actor filters, refresh button, compact table (When / Actor + IP / Action pill / Resource + ID / JSON details).

## Implemented (2026-04-21)
- **Hide past-time departures on same-day search** — `GET /api/search` now adds a `departure_time >= now` filter when `date` matches today in MY/SG local time (UTC+8), so users can't book a bus that has already left. Future dates are unaffected.
- **Login brute-force protection** — MongoDB-backed rate limiter on `POST /api/auth/login`.
  - Collection `login_attempts` (TTL index auto-expires rows after 15 min).
  - Per identifier (`{ip}:{email}`): **5 failed attempts / 15 min** → HTTP 429.
  - Per IP overall: **20 failed attempts / 15 min** → HTTP 429 (stops distributed account scans).
  - Successful password verification clears the counter for that IP+email.
  - Response includes `Retry-After: 900` header + friendly message.
  - IP detection respects `X-Forwarded-For` (Kubernetes ingress / Nginx).
  - Verified end-to-end: attempt 6 correctly returns 429; successful login resets counter; header present.
- **Extended rate limiting to register + 2FA**
  - New `auth_throttle` collection with per-document TTL (`expires_at` field + `expireAfterSeconds=0`) so each scope can have its own window.
  - Generic helpers: `_check_auth_throttle`, `_record_auth_failure`, `_clear_auth_attempts`.
  - `POST /api/auth/register`: **10 signups / hour / IP** (prevents bot spam). Verified: 11th returns 429.
  - `POST /api/auth/2fa/verify`: **5 wrong codes / 15 min / IP+user** (prevents TOTP guessing after password leak). Success clears the counter. Verified: 6th returns 429.
- **Password strength enforcement**
  - Backend `_validate_password_strength()` + blocklist of ~100 common passwords (top-100 SecLists + local variants).
  - Rules: min 8 chars, ≥1 uppercase, ≥1 lowercase, ≥1 digit, not in blocklist.
  - `RegisterBody.password` min_length raised from 6 → 8.
  - Returns HTTP 400 with specific reason (e.g. "Password must contain at least one uppercase letter.", "This password is too common. Please pick something unique.").
  - Frontend Register page rewritten with live strength meter (5-segment bar: Weak/Fair/Good/Strong colors) + interactive checklist; submit button disabled until all 5 rules pass.
  - Verified: 4 negative scenarios return 400, strong password succeeds. Screenshots confirm bar/checklist/button states.

## Implemented (2026-04-21 · continued)
- **Forgot / Reset password flow** (Resend-powered)
  - `POST /api/auth/forgot-password` — always returns 200 with generic message (no user enumeration). Generates a bcrypt-hashed one-time token, stored in `password_reset_tokens` with per-doc TTL (`expires_at`, 1 hour). Invalidates any prior unused tokens for that user. Sends reset email via Resend. Throttled: 5/hour/IP.
  - `POST /api/auth/reset-password` — bcrypt-checks the supplied raw token against active candidates, enforces the full password-strength policy on the new password, marks token used (single-use), clears any active login lockouts so the user can sign in immediately. Throttled: 10/hour/IP.
  - New Resend template `send_password_reset()` — Swiss/brutalist layout matching the ticket email, single CTA button, plain-text fallback link.
  - Frontend pages: `/forgot-password` (generic "check your inbox" confirmation), `/reset-password?token=…` (new password + confirm, reuses 5-segment strength meter + 5-check list from Register, blocks submit until strength + match both pass). Added "Forgot password?" link under the Login password field.
  - Extracted shared strength util to `/app/frontend/src/lib/password-strength.js` and reused on both Register and Reset pages.
  - Verified end-to-end: register → forgot → reset with valid token (200) → token reuse blocked (400) → old password fails (401) → new password works (200). Bad tokens, weak passwords, and enumeration probes all handled correctly.
- **Bus seat map redesign + 2+1 layout support**
  - Backend: `CreateScheduleBody`/`BulkScheduleBody` replaced `rows` with `total_seats` (12–60). New `_layout_for_bus_type()` maps bus class → layout config: **VIP → 2+1**, Standard/Executive → 2+2. Schedules persist `layout_config`. `GET /schedules/{id}` now emits `layout` + `layout_config` + `seats_per_row`; supports partial last rows cleanly (e.g. 27-seat VIP = 9 rows of 3).
  - Frontend (`SeatSelection.jsx` + `index.css`): new bus-shell aesthetic — rounded windshield arc, driver steering-wheel icon, headrest-notched seat pills, aisle gap, EXIT door, "REAR · ENGINE" footer, hover-lift animation. Category-aware fill (adult = red, child = teal) so travellers see their mix at a glance. Supports 2+2 and 2+1 in one component.
  - Admin form replaced "Rows" input with "Total seats (capacity)" + inline hint explaining the layout mapping.

## Implemented (2026-04-29) — Single-currency Stripe billing (MYR)
- `/api/payments/checkout` now **always bills in MYR** regardless of booking display currency. SGD bookings auto-convert at checkout time using the live `sgd_to_myr_rate` (default 3.50, editable in Admin → Add Schedule).
- `payment_transactions` now stores both the MYR charged amount + the original display amount/currency + the fx rate used, so receipts and refunds remain accurate.
- Cancellation refunds (`/api/bookings/{id}/cancel`) now use the **actual MYR amount Stripe charged** from `payment_transactions`, not the booking's display total — fixes the bug where SGD bookings would have refunded the wrong amount.
- Cancellation quote endpoint also returns the real MYR refund amount so the modal shows what'll appear on the customer's statement.
- All 3 payment methods (Card, GrabPay, FPX) now available regardless of display currency, since the actual charge is always MYR. FPX must still be activated in the merchant's Stripe Dashboard.
- Frontend Passengers page shows a small note "CARD WILL BE CHARGED IN MYR · 1 SGD ≈ RM 3.50" below the secure-checkout label for SGD bookings.
- Verified end-to-end: SGD 18 booking → Stripe session created in MYR 63.00 (1:3.5 conversion), display preserved as SGD.

## Implemented (2026-04-29) — New Header (Option A · Minimal + Avatar Dropdown)
- Public nav trimmed from 6 items to **2** (Search · Feedback). All account actions consolidated into a single avatar pill on the top-right.
- Avatar pill shows initials in a coloured circle (blue for passenger, red for admin) + first name + small ADMIN role tag for admins.
- Click pill → dropdown menu: full name + email, then My Bookings · Security · 2FA · Change password, then Admin console (red, only for admins), then Log out. Click-outside and Escape close the menu. Fully keyboard accessible (`role=menu`, `aria-expanded`, `aria-haspopup`).
- Mobile: avatar-only collapse — name + role tag hide below the `sm` breakpoint while the pill stays clickable.
- Verified end-to-end: closed/open dropdown, all 5 menu items render, click-outside closes, Escape closes, mobile viewport collapses cleanly.

## Implemented (2026-04-29) — Last login banner + Password quick-link
- Backend: `/api/auth/login` and `/api/auth/2fa/verify` now go through a shared `_record_login(user, ip)` helper that rotates `last_login_at`/`last_login_ip` → `previous_login_at`/`previous_login_ip` and writes the new values. Login fields no longer rotate on a failed 2FA attempt (pre-existing minor bug fixed in passing).
- Frontend: new `<LastLoginBanner/>` on the user Dashboard shows "LAST SIGN-IN · 3 hr ago · from 203.0.113.42 · NOT YOU? CHANGE PASSWORD →". Hidden until the second login (no `previous_login_at`), bank-style.
- Header gets a new "Password" nav item (`/change-password`) for any logged-in user.
- Verified end-to-end: login → forced change → /admin → /dashboard renders the banner with relative time + IP; Header link reaches `/change-password`.

## Implemented (2026-04-29) — Forced password change on first login
- New `POST /api/auth/change-password` endpoint: verifies current password, enforces strength, blocks reuse, clears `must_change_password` flag.
- New `/change-password` route + `<ChangePassword/>` page with strength meter, match check, and helpful "first-time login" notice when forced.
- New `<ForcePasswordChangeGate/>` component wrapping the router: any authed user with `must_change_password=true` is locked into `/change-password` until they change it (escape hatch: log out).
- Login & 2FA flows updated to route to `/change-password` directly when the flag is set (no flash through `/dashboard`).
- Bootstrap admin (`admin@starqistna.com`) is seeded with `must_change_password=true` so the first ever login is forced through the new password screen.
- Verified end-to-end via Playwright: login → `/change-password` → escape attempt to `/admin` bounces back → set `NewStr0ngPass!2026` → land on `/admin` → logout → re-login with new password → straight to `/dashboard` with no redirect.

## Implemented (2026-04-29) — Security & Performance Audit (Day 1)
- **🔴 Security blockers fixed:**
  - **C1** JWT_SECRET rotated from placeholder string to a 128-char hex random. Old tokens auto-invalidated.
  - **C2** CORS misconfiguration fixed: `allow_credentials=False` (we use Bearer tokens, not cookies). Closes the cross-origin token-theft hole.
  - **C3** Bootstrap admin password moved to `BOOTSTRAP_ADMIN_PASSWORD` env var. No more plaintext password in startup logs. New admin accounts get `must_change_password=True` flag (UI hook deferred).
  - **C4** `GET /api/bookings/{id}` now enforces ownership: owner OR admin OR guest with matching `?email=` query (matches `contact_email`). PaymentCallback's "View booking" link auto-appends `?email=` for guest checkouts. BookingDetail reads it from URL.
- **🟠 P1 hardening:**
  - **H1** ReDoS / regex injection fixed in `/api/terminals?q=` and `/api/admin/audit-logs?actor_email=` via `re.escape()` + length cap.
  - **H2** `/api/search` N+1 → single aggregation pipeline (`$group $in $sum`). 200 round-trips → 1.
  - **H4** `/api/payments/options/{id}` now requires auth or matching `?email=` (was open).
  - **H5** `/api/popular/now` cached for 30s in-memory (TTLCache). 16+ DB queries → 0 on cache hit.
- **🟡 P2 perf:**
  - **M1** `/api/bookings/me` terminal lookups → single `$in` batch.
  - **M2** `_finalize_booking` terminal lookups parallelised via `asyncio.gather`.
  - **M3** `/api/admin/stats` (5 counts) and `/api/admin/feedback` (4 counts) parallelised via `asyncio.gather` (~5× faster).
  - `_payment_options_for(currency)` wrapped in `@lru_cache` (tiny input space, read-only).
  - `/api/terminals` (no `q`) cached 5min, busted on terminal create/update/delete.
  - `/api/settings` cached 1min, busted on `update_settings`.
- **M8** New indexes: `bookings.created_at`, `bookings.status`, schedules compound extended to include `departure_time`.
- Verified: 44/44 backend tests pass (32 new audit + 12 cancellation regression). All 9 admin lazy tabs + Dashboard tabs render correctly. CORS preflight no longer emits credentials header. JWT rotation breaks old tokens as intended.

## Implemented (2026-08-02)
- **Admin recovery + Google OAuth swap + deploy automation**
  - New `backend/scripts/reset_admin_password.py` CLI tool + `RESET_ADMIN_ON_BOOT=true` env flag to unblock a locked-out super-admin without dropping the DB.
  - `_seed_admin()` now detects env↔DB password drift on every boot and logs a big recovery WARNING.
  - Google Auth switched from Emergent-managed to own OAuth Client (Client ID: `426301430644-uq2k54p7l3les28e4t3okeltl3ucu2n3.apps.googleusercontent.com`). Users see "Continue to starqistna.com" branded consent. Backend endpoint `POST /api/auth/google/callback` exchanges code+client_secret with Google, verifies id_token via `google-auth`, upserts user (auto-link by email), issues JWT. Respects existing 2FA.
  - New `scripts/deploy.sh` (repo root) — idempotent VPS deploy: syncs `.env.example` → `.env` (never overwrites values), pip install, yarn build, systemctl restart backend + reload nginx.
  - `.env.example` files committed (with real public Client ID baked in). `.gitignore` cleaned + exception `!**/.env.example` added.
  - Removed the admin demo credentials hint from `Login.jsx` (no more `ADMIN DEMO · admin@starqistna.com / Admin@123` visible on production).

- **Routes + Stops data model (KL-SG focused)** — first slice of the multi-stop trip system.
  - Extended `terminals` schema with: `landmark_address, lat, lng, cts_code, is_pickup, is_dropoff` — all optional, backward-compatible with existing 14 terminals.
  - New `routes` collection: `code, name, origin_city, destination_city, direction, is_active, boarding_stops[{terminal_id, offset_min}], alighting_stops[{terminal_id, offset_min}], pairings[{pickup_id, dropoff_id, adult_fare, child_fare, senior_fare, oku_fare, currency, cts_route_code}]`. All fares default to RM 55; each pairing individually overridable.
  - Endpoints (all admin-gated): `GET/POST/PATCH/DELETE /api/admin/routes` and `GET /api/admin/routes/{id}`. Validation ensures every pairing references a stop that is actually in the route's boarding/alighting arrays.
  - Admin UI:
    - `TerminalsTab.jsx` rewritten as **"Stops"** — inline **Edit** action, landmark address, GPS, CTS code, pickup/dropoff toggle chips, filter search.
    - New `RoutesTab.jsx` — list of routes on the left, editor on the right with basic info, side-by-side pickup/dropoff stop pickers (add/remove/re-order by offset minute), and a full **pairings matrix** with per-cell fare editor (A/C/S/O + currency + CTS route code). Bulk actions: "Fill empty cells" (default RM 55), "Clear all".
  - Verified: full CRUD via curl (create → GET → PATCH fare → DUPLICATE guard → validation error → DELETE). Playwright confirmed end-to-end UI flow: login → admin → routes tab → new route form → add pickup + dropoff stops → enable pairing cell.

## Implemented (2026-08-15) — City search shows specific pickup + drop-off
- Fixed: on city-level searches ("Any stop in KL → Any stop in Singapore") each departure listed the trip time but never told the passenger WHICH specific terminal to board at.
- Backend `/api/search` now enriches every result schedule with a compact `from_terminal` + `to_terminal` object (`id`, `code`, `name`, `city`, `landmark_address`) via a single batched terminals lookup — no N+1.
- Search results header now labels city-level context ("4 pickup stops → 2 drop-off points") and displays a blue banner: "City-level search — each trip below shows the specific pickup + drop-off stop."
- Every schedule row on Search results has full-width green "PICKUP" and red "DROP-OFF" badges with terminal name, code, city and landmark address (when present).
- Clicking Select now passes the schedule's SPECIFIC pickup + drop-off (not the city aggregate) through `booking-store`, so downstream pages know the exact terminal.
- Seat Selection page adds the same PICKUP / DROP-OFF badges beneath the trip title so the passenger sees where to board even after they've picked a departure.

## Implemented (2026-08-15) — Bus Types admin catalog
- New `bus_types` collection with fields: `id`, `name` (unique), `seat_count` (12–60), `image_url` (optional), `description` (optional), `layout` (auto-derived: names containing "VIP" → 2+1, else 2+2), `created_at`.
- Endpoints: `GET /api/bus-types` (public — used by schedule forms), `GET/POST/PATCH/DELETE /api/admin/bus-types` (admin-gated + audit-logged).
- Safeguards: unique name enforcement (409-style 400 message), Pydantic `ge/le` on `seat_count`, and DELETE blocked when any schedule still references that bus type name.
- Startup seed: idempotently adds `VIP 27` (27), `Executive` (40), `Standard` (40) if missing. Existing installations don't lose customised seat counts.
- Schedule form now uses a single **Bus Type** dropdown — picking a bus auto-fills `total_seats`. Admin can still override manually per trip. The old 3-button "Coach class" picker is retired.
- `EditScheduleModal` uses the same dropdown; legacy bus_type values not in the current catalog still render (labelled "legacy") so historical schedules aren't clobbered.
- Backend `bus_type` field switched from a fixed `Literal[...]` to free `str` on both `CreateScheduleBody` and `BulkScheduleBody` — required so custom admin-created bus types are accepted.
- New tests: 5 pytest cases (`test_bus_types.py`) validating seed, auth, CRUD cycle, duplicate guard, in-use delete guard, and seat-count bounds validation. All green.

## Implemented (2026-08-15) — Rebrand: Star Qistna → Qistna Express
- All user-facing text updated across frontend + email tickets from "Star Qistna" to "Qistna Express".
- Footer copyright changed to `© {YEAR} QISTNA EXPRESS PVT LTD · ALL RIGHTS RESERVED`.
- HTML title + meta description updated (browser tab + SEO).
- Calendar .ics file now generates `PRODID:-//Qistna Express//Booking//EN`, download name `QistnaExpress-{ref}.ics`, event summary `Qistna Express · {from} → {to}`.
- Email templates (booking confirmation, reset password, schedule change, admin invite, feedback, cancellation) all rebranded — headers + subject lines + footer text.
- TOTP issuer (Authenticator app label) changed from `Star Qistna` → `Qistna Express`.
- Seed data (`bus_operator`, admin `full_name`, `OPERATORS` list) updated; startup migration `_migrate_operator_names` now migrates existing schedules to `Qistna Express` and updates the seeded admin's `full_name` if it still reads `Star Qistna Admin`.
- Kept intact (infrastructure): domain `starqistna.com`, email addresses `support@starqistna.com`, admin login email `admin@starqistna.com`, `password-strength.js` blocklist entry `starqistna`, GoHub `STARQISTINA` OTA code (external TBS identity).
- ⚠️ **Action needed by client**: replace `/app/frontend/public/logo.png` with the new Qistna Express logo image. Header + footer still render the old Star Qistna logo file.

## Implemented (2026-04-27)
- **Dashboard "My cancellations" filter** — Dashboard now separates bookings into three tabs: **Upcoming · Past · Cancelled**, each with its own count badge. Cancelled bookings (status `cancelled_refunded` or `cancelled_burned`) are pulled out of the date-based upcoming/past split so they don't clutter live trips. Stats row updated to 4 cards (Upcoming / Past / Cancelled in signal-red / "Plan a trip" CTA). Verified via Playwright: all 3 tabs switch and render their respective list panels.
- **Admin.jsx refactor + lazy loading** — split the 1,137-line monolith into a slim shell + 9 self-contained tab components under `/app/frontend/src/pages/admin/tabs/` (`BookingsTab`, `PaymentsTab`, `SchedulesTab`, `AddScheduleTab`, `TerminalsTab`, `PromoCodesTab`, `FeedbackTab`, `AuditLogTab`, `AdminsTab`). Each tab owns its own data fetching, local form state, and event handlers. Now wrapped in `React.lazy` + `Suspense` so each tab's JS chunk only downloads when its tab is clicked — initial admin bundle is much smaller. All `data-testid` attributes preserved.
- **Cancel Booking** — new flow on the booking detail page.
  - Policy: cancel ≥24h before departure → full Stripe refund · cancel <24h → ticket burned (no refund).
  - Backend: `GET /api/bookings/{id}/cancellation-quote` (preview) and `POST /api/bookings/{id}/cancel` (commit). Owner-or-admin only. `_hours_to_departure` computes against MY/SG TZ (UTC+8).
  - Refund path: looks up the latest paid `payment_transactions` for the booking, retrieves the PaymentIntent via `stripe.checkout.Session.retrieve`, then `stripe.Refund.create(...)` with `idempotency_key=refund:{booking_id}`. Updates txn → `payment_status='refunded'`. Booking status → `cancelled_refunded`. Free promo bookings (total=0) skip Stripe and still resolve to `cancelled_refunded` for clean UX.
  - Burn path: no Stripe call. Booking status → `cancelled_burned`.
  - Both paths: delete `seat_locks` so seats are freed for re-sale, write an audit log entry, fire-and-forget cancellation email via Resend (`send_booking_cancelled` + `_render_cancelled_html`).
  - Frontend: `<CancelBookingButton/>` modal shows the live time-to-departure, refund threshold, and either a green "RM X refunded" panel or a red "ticket burned" warning. Booking detail page shows green/red banner + updated status badge after cancellation. Dashboard list and detail page status badges support the new `cancelled_refunded` / `cancelled_burned` states.
  - Verified: 12/12 backend pytest cases pass (auth gates, owner check, ≥24h vs <24h logic, fake-Stripe rollback safety, seat_lock cleanup, free-booking edge case). Admin lazy-loaded tabs all render their unique panels.

## Backlog / next tasks
### P0 — Recently resolved
- **[2026-08-11] Trip stop date (schedule expiry) consolidated on existing `end_date` field.**
  - Admin can set/clear/edit `end_date` per schedule via the new edit modal — same field the bulk-create form already writes at generation time (no duplicate field).
  - Public search filters out schedules where `end_date < requested date`. `POST /bookings` returns HTTP 410 (`schedule_retired`) if a customer tries to book a retired schedule directly.
  - Frontend badge on admin schedule rows: amber `Ends 2026-12-31` (future) / red `Ends 2026-08-01` (past).
  - Existing bookings on retired schedules stay valid — customers can view their tickets, only new sales blocked.

- **[2026-08-11] Group 2: Edit Existing Trips with Notification (P0 admin pain point).**
  - `GET /api/admin/schedules/{id}/impact` — returns confirmed booking count, passenger count, count of bookings with CTS QRs already issued, sample refs. UI shows this as a blast-radius banner BEFORE the admin edits.
  - `PATCH /api/admin/schedules/{id}` extended with `notify_passengers: bool`. When true AND departure_date/time changed, dispatches per-booking emails via new `send_schedule_change()` mailer (old→new time side-by-side, CTA to booking page).
  - Edit modal in `SchedulesTab` — full field set (times, fares, seats, bus type, route, trip_no, end_date). Notify checkbox appears ONLY when time/date changed AND there are confirmed bookings. Fare/seat-only changes don't trigger emails.

- **[2026-08-11] Group 1: Google users can now set a password (Dashboard prompt).**
  - `POST /api/auth/set-password` — first-time password creation for authenticated users; 409 if user already has one.
  - `/auth/me`, `/auth/login`, `/auth/register`, Google callback now all include `has_password: bool`.
  - `/auth/forgot-password` fixed — Google users can now use it (previously silently skipped when `password_hash` was empty).
  - Dashboard renders a dismissible `SetPasswordCard` for `has_password: false` users. Non-blocking.

- **[2026-08-11] CTS QR with gopass logo — end-to-end (Phase A polish).**
  - `GoHubClient.fetch_qr_image(qr_value)` — POSTs to `GOHUB_QR_IMAGE_URL` (default `https://gopassqr.nssit.com.my/QrCodeWithLogo`), returns branded PNG bytes or `None` on any failure.
  - `_issue_gohub_tickets` fetches per-seat branded image in parallel, persists as `qr_image` (base64 data URL) on each ticket entry.
  - `BookingDetail.jsx` + `PaymentCallback.jsx` render `<img>` with branded PNG when present, fall back to local `<QRCodeSVG>` otherwise.
  - Email attachments use branded PNG when available (same CID structure `qr-{seat}`).
  - `PaymentCallback.jsx` gained a follow-up CTS poll (up to 12s after payment "paid") so the branded QR swaps in live once the background CTS task completes.
  - **Non-TBS routes** (terminals without `cts_code` OR schedules without `trip_no`) skip CTS entirely — pre-flight `gohub_status='skipped'` sets in immediately, no wait, plain-QR fallback shown.

- **[2026-08-11] Stripe payment reconciliation (webhook-independent safety net).**
  - Background reconciler task started at app startup: every 60s scans `payment_transactions` where `status='initiated'` and age is 30s..24h; asks Stripe for real status; finalizes any that are `paid` (calls `_finalize_booking` idempotently).
  - `POST /api/admin/payments/reconcile` — force-sweep on demand. `POST /api/admin/payments/reconcile/{session_id}` — target one.
  - CLI: `python scripts/reconcile_payments.py [--session cs_XYZ | --booking BK123 | --dry-run]`.
  - `PaymentCallback.jsx` polling extended 16s → 40s (accommodates GrabPay/FPX settle delay), plus a manual "Verify payment now" button on timeout.
  - `Passengers.jsx` differentiates between booking-creation vs Stripe-checkout errors; surfaces the exact backend/Stripe message ("Payment method GRABPAY not activated" etc.) instead of a generic "Could not create booking".
  - `/payments/checkout` now returns HTTP 422 with a friendly message when Stripe rejects a payment method (previously bare 500).

- **[2026-08-11] Email service resilience.**
  - `_resend_ready()` helper — rejects placeholder keys like `re_replace_me` / `changeme` / non-`re_`-prefixed strings so misconfigured deploys log clear warnings instead of failing at Resend.
  - All 5 email-send sites (booking confirmation, cancellation, password reset, schedule change, etc.) now guard behind `_resend_ready()`.

- **[2026-08-05] GoHub Phase 1 (spec-compliance) + Phase 2 (Stripe → CTS wiring) shipped.**
  - Client refactored to full CTS OnlineQR v1.2.11 conformance: dates `DD/MM/YYYY`, times `HHMMSS`, multi-seat `<detail>` per `<ticket_details>`, mandatory `opetickno` + `name`, one-shot `getOnlineQR_V2` method added.
  - **`opetickno` format** locked as `SQ-{booking_ref}-{seat_no}` (uppercase, ≤ 20 chars — validated).
  - **Signature formula verified live** as `md5(OTACode + DD/MM/YYYY + Password)` **lowercase** hex. Encoded in `_md5_signature()` after live sweep of ~200 variants via `scripts/gohub_sign_sweep.py`.
  - Origin counter code confirmed as `TBS`. Destination `GMC` verified live in production (real QR `NQR,QISTINA,...` issued for booking `SQFE29FD25` after TBS ops uploaded the rate matrix).
  - **Phase 2 wiring**: `_finalize_booking` fires `asyncio.create_task(_issue_gohub_tickets(...))` after Stripe webhook. Pre-flight guards: `GOHUB_ENABLED`, `schedule.trip_no`, `terminal.cts_code` on both ends → skips gracefully if any missing. On success persists `booking.gohub_status='confirmed'` + `gohub_tickets=[{seat_number, opetickno, tickno, qr, qr_image}, ...]`. On `GoHubError`/unexpected exception: `gohub_status='failed'` + `gohub_error={code, message}`. Never propagates back to Stripe webhook (fire-and-forget).
  - **Admin retry endpoint**: `POST /api/admin/bookings/{id}/gohub/retry` — for reissuing tickets after TBS uploads the rate.
  - **60 pytest cases green** (35 client + 8 Phase 2 wiring + 7 Stripe webhook + 6 email QR rendering + 4 misc).

- **[2026-02-02] Stripe webhook hardened.** `/api/webhook/stripe` returns 400 only on signature failure; 200 on all business errors (unknown event types acknowledged with `{"ignored": <type>}`). Backed by 7 pytest cases in `test_stripe_webhook.py`.

### P1 — Next up
- **Group 3: Bus Type management (simple version)** — new `bus_types` collection (name + seat_count + optional image), CRUD tab in admin, dropdown replaces manual seat_count in schedule form. Legacy schedules keep their existing seat count.
- **Group 4: Admin UI reorganization** — sidebar nav with 4 sections (Operations / Trip Setup / CTS Integration / System), landing Dashboard with today's bookings + revenue + upcoming trips, delete duplicate `stops` tab, merge `add-schedule` into Schedules as a "+ New" button.
- Email ticket delivery — already working end-to-end (test booking `SQA15D2787` received branded PNG QR).
- iPay88 integration — parked (Stripe covers Card + GrabPay + FPX; user is happy).
- React Native mobile app — deferred until web system is perfected (user request).

### P2
- **`_WithHandling` variants** (getOnlineQRWithHandling / reserveOnlineQRWithHandling) — v1.2.10 additions with management-fee support. Not needed until TBS enables handling charges on our operator config.
- **Route Map Preview** (Leaflet) in Admin Routes editor — visualise boarding/alighting markers.
- Promo code per-user usage limit (e.g. "first-time users only").
- Multi-language (BM / EN / ZH), push notifications, seat preferences.
- AI Customer Support Chatbot + WhatsApp fallback widget.
- Resend domain verification (SPF/DKIM/DMARC for `starqistna.com`) — currently sending from `onboarding@resend.dev` which will hit spam eventually.

### P3
- Failure retry queue for GoHub (`gohub_retry_queue`) — auto-reissue on TBS outage recovery.
- Mobile horizontal scrollable rail for Popular Now widgets.
- Refactor `server.py` into route-based controllers (Auth / Admin / Bookings / Public) — currently >4100 lines.
- Bulk "retire trip after date X" tool — deletes/expires every future schedule matching a trip_no.

## Diagnostic tools shipped this session
- `backend/scripts/gohub_sign_debug.py` — prints signature inputs (masked pw) + all common variants
- `backend/scripts/gohub_sign_sweep.py` — fires ~200 signature variants live at TBS; identifies the accepted formula
- `backend/scripts/gohub_dest_sweep.py` — sweeps candidate destination counter codes to find valid CTS routes
- `backend/scripts/gohub_probe.py` — full reserve → confirm → query → cancel probe (plus `--one-shot` mode for the Phase 2 getOnlineQR_V2 path)
- `backend/scripts/reconcile_payments.py` — force-reconcile stuck Stripe payments (all, one session, or one booking; `--dry-run` supported)

## Live production integrations status (2026-08-11)
| System | Status |
|---|---|
| Stripe (Card + GrabPay + FPX) | ✅ live, webhook active, MYR-billing, background reconciler as safety net |
| Resend (email) | ✅ live via `onboarding@resend.dev`, real API key set on VPS — domain verification pending |
| Google OAuth | ✅ live, users can now also set a password via Dashboard |
| TBS CTS OnlineQR | ✅ live for `SQ001 · TBS → GMC` (real branded QR issued end-to-end) |
| Reconciler background loop | ✅ starts at app startup, sweeps every 60s |

## Notes
- Admin login: `admin@starqistna.com` / `Admin@123` (auto-seeded)
- Stripe: production keys in VPS `/root/app/backend/.env`
- CORS open to `*` for MVP — tighten before production
- VPS: `sq@srv1598663` (Hostinger, `starqistna.com` served via Nginx + `starqistna-backend.service` systemd unit)
- Deploy on VPS: `cd ~/app && git pull && ./scripts/deploy.sh` (auto-detects systemd/PM2/supervisord)
