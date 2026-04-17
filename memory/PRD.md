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

## Backlog / next tasks
### P1
- iPay88 integration (need merchant credentials: Merchant Code, Merchant Key, environment)
- Email ticket delivery (Resend / SendGrid) on `payment_status=paid`
- QR code on booking detail page (for boarding)
- Round-trip booking (return journey)
- Admin: edit/delete schedules, terminal CRUD, refund handling

### P2
- React Native mobile app consuming same APIs
- Operator portal (bus operators self-service)
- Promo codes / discounts
- Multi-language (BM / EN / ZH)
- Push notifications for delays
- Seat preferences (window/aisle saved to user profile)

## Notes
- Admin login: `admin@transit.my` / `Admin@123` (auto-seeded)
- Stripe key: `sk_test_emergent` in backend/.env
- CORS open to `*` for MVP — tighten before production
