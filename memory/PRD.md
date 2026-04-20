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
- **Testing**: iterations 3–8 all green.

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
