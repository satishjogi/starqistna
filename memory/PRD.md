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

## Backlog / next tasks
### P1
- iPay88 integration (need merchant credentials: Merchant Code, Merchant Key, environment)
- Email ticket delivery (Resend / SendGrid) with QR attached on `payment_status=paid`
- React Native mobile app — deferred until web system is perfected (user request)

### P2
- Operator portal — skipped (single-company, not needed per user)
- Promo code per-user usage limit (e.g. "first-time users only")
- Admin: edit/delete schedules, terminal CRUD, refund handling
- Multi-language (BM / EN / ZH), push notifications, seat preferences

## Notes
- Admin login: `admin@transit.my` / `Admin@123` (auto-seeded)
- Stripe key: `sk_test_emergent` in backend/.env
- CORS open to `*` for MVP — tighten before production
