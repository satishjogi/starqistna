# Star Qistna — Architecture Snapshot (v1.0 · April 2026)

## Stack
| Layer | Technology |
|---|---|
| Frontend | React 18 (CRA) + Tailwind + Shadcn UI components |
| Routing | React Router 6 |
| Backend | FastAPI + Uvicorn (async) |
| Database | MongoDB 8 (Motor async driver) |
| Auth | JWT (HS256) + bcrypt + optional TOTP 2FA |
| Payments | Stripe Checkout (Card · GrabPay · FPX) |
| Email | Resend (transactional) |
| Social Auth | Emergent-managed Google OAuth (preview) |
| QR | `qrcode` Python lib (email attachment) + `qrcode.react` (frontend) |

## File structure

```
/app/
├── backend/
│   ├── server.py              # ~2,600 lines · all routes, models, auth, business logic
│   ├── email_service.py       # ~365 lines · Resend templates: ticket, password reset, admin invite, feedback
│   ├── requirements.txt
│   ├── tests/                 # pytest regression suite
│   └── .env                   # MONGO_URL, DB_NAME, JWT_SECRET, STRIPE_*, RESEND_API_KEY, PUBLIC_APP_URL, CORS_ORIGINS
│
├── frontend/
│   ├── package.json
│   ├── public/
│   │   ├── index.html
│   │   ├── favicon.svg        # Stylized brand-red 5-point star
│   │   └── logo.png
│   └── src/
│       ├── App.js             # All routes
│       ├── index.css          # Brand tokens + bus-shell + seat styles
│       ├── components/
│       │   ├── Header.jsx
│       │   ├── Footer.jsx
│       │   ├── PopularNow.jsx          # 🔥 live-countdown widget
│       │   ├── NextTripWidget.jsx      # Dashboard upcoming-trip card
│       │   ├── GoogleAuthButton.jsx
│       │   └── ui/...                  # Shadcn primitives
│       ├── lib/
│       │   ├── api.js                  # axios instance with REACT_APP_BACKEND_URL
│       │   ├── auth.jsx                # AuthProvider + useAuth hook
│       │   ├── booking-store.js        # localStorage flow state
│       │   ├── price.js                # formatPriceWithMyr (SGD → MYR bracket)
│       │   └── password-strength.js    # Shared analyzer + COMMON_PASSWORDS list
│       └── pages/
│           ├── Home.jsx                # Hero + search + Popular Right Now + popular routes
│           ├── SearchResults.jsx       # Filtered schedules · "no buses today → try tomorrow" rescue
│           ├── SeatSelection.jsx       # Portrait floor-plan bus, 2+1 / 2+2, RHD
│           ├── Passengers.jsx          # Contact + per-passenger details + checkout
│           ├── PaymentCallback.jsx     # Stripe return + status poll
│           ├── BookingDetail.jsx       # Single booking · QR + share
│           ├── Dashboard.jsx           # User's bookings
│           ├── Login.jsx               # + Forgot password link + Google
│           ├── Register.jsx            # Strength meter + intl phone
│           ├── ForgotPassword.jsx
│           ├── ResetPassword.jsx
│           ├── AdminInviteAccept.jsx   # New admin sets password from email link
│           ├── AuthCallback.jsx        # Google OAuth landing
│           ├── Feedback.jsx            # Customer feedback form
│           ├── Security.jsx            # 2FA setup
│           ├── Admin.jsx               # 9-tab control panel (split planned)
│           ├── Terms.jsx · Privacy.jsx · RefundPolicy.jsx
│           └── ...
│
└── memory/
    ├── PRD.md                 # Canonical requirements
    ├── CHANGELOG.md           # Version history (this snapshot lives here too)
    ├── ARCHITECTURE.md        # ← this file
    └── test_credentials.md
```

## MongoDB collections

| Collection | Purpose | TTL? |
|---|---|---|
| `users` | Accounts (incl. admins). Fields: id, email, password_hash, full_name, phone, is_admin, role (`super_admin`/`admin`/null), is_active, totp_enabled, totp_secret, google_id, last_login_at, created_at | — |
| `terminals` | Cities + sub-terminals. `code`, `name`, `city`, `state`, `country` | — |
| `schedules` | Each trip: from/to_terminal_id, departure_date, departure_time, arrival_time, bus_type, **adult_fare**, **child_fare**, total_seats, layout_config (`2+1`/`2+2`), currency, created_at | — |
| `seat_locks` | Reserved seats. Partial unique index on `(schedule_id, seat_number)` for `{locked,booked}` → atomic, prevents double-booking | 10 min for `locked` |
| `bookings` | Confirmed bookings. reference, user_id, schedule_id, passengers[], total_price, currency, status, payment_session_id, created_at | — |
| `payment_transactions` | Stripe session tracking. Unique on session_id | — |
| `promo_codes` | Discount codes. Unique on code | — |
| `audit_logs` | Every admin write action with actor_email + IP + details | — |
| `login_attempts` | Brute-force tracker | TTL 15 min |
| `auth_throttle` | Generic throttle (register, 2fa, reset, feedback) | per-doc TTL via `expires_at` |
| `password_reset_tokens` | bcrypt hash + SHA-256 fingerprint for O(1) lookup | per-doc TTL (1 hr) |
| `admin_invites` | bcrypt + fp + role + expires_at | per-doc TTL (7 days) |
| `feedback` | Customer messages. reference (F-XXXXXX), category, rating, status (new/in_progress/resolved) | — |
| `app_settings` | `{id: "global", sgd_to_myr_rate}` for SGD → MYR display | — |

## API surface (55 endpoints)

### Public
- `GET /api/terminals` — grouped + flat
- `GET /api/search` — schedules for date (filters past times for today MY)
- `GET /api/schedules/{id}` — seat map + layout
- `GET /api/popular/now` — time-aware soonest departures (UTC+8 aware)
- `GET /api/settings` — sgd_to_myr_rate (no auth)
- `GET /api/promo-codes/validate`
- `POST /api/seats/lock`, `POST /api/seats/unlock`
- `POST /api/bookings`, `POST /api/bookings/confirm`
- `POST /api/payments/checkout`, `GET /api/payments/status/{session_id}`
- `POST /api/webhook/stripe` (HMAC verified)
- `POST /api/feedback`
- `POST /api/auth/register`, `POST /api/auth/login`
- `POST /api/auth/google/session`
- `POST /api/auth/2fa/verify`
- `POST /api/auth/forgot-password`, `POST /api/auth/reset-password`
- `POST /api/admin/admins/accept-invite` (token-gated)

### Authenticated
- `GET /api/auth/me`
- `GET /api/my-bookings`, `GET /api/bookings/{ref}`
- `POST /api/auth/2fa/setup`, `POST /api/auth/2fa/disable`

### Admin (`is_admin: true` required)
- `GET /api/admin/admins` (any admin)
- `GET /api/admin/bookings`, `GET /api/admin/payments`
- `GET /api/admin/schedules`, `POST /api/admin/schedules`, `POST /api/admin/schedules/bulk`, `DELETE /api/admin/schedules/range`
- `POST /api/admin/terminals`, `PATCH /api/admin/terminals/{id}`, `DELETE /api/admin/terminals/{id}`
- `POST /api/admin/promo-codes`, `PATCH /api/admin/promo-codes/{id}`, `DELETE /api/admin/promo-codes/{id}`
- `GET /api/admin/audit-logs`
- `GET /api/admin/feedback`, `PATCH /api/admin/feedback/{id}`
- `PATCH /api/admin/settings`

### Super-admin only
- `POST /api/admin/admins/invite`
- `PATCH /api/admin/admins/{id}/role`, `PATCH /api/admin/admins/{id}/active`, `DELETE /api/admin/admins/{id}`
- `DELETE /api/admin/admins/invites/{id}`

## Security posture

| Threat | Mitigation |
|---|---|
| Brute-force login | 5/15min per IP+email · 20/15min per IP |
| Signup spam | 10/hour per IP |
| TOTP guessing | 5/15min per IP+user |
| Password reset abuse | 5/hour per IP, single-use tokens, 1-hr expiry, O(1) fp lookup |
| Common passwords | Backend blocklist (~100) + client mirror + strength rules (8+ chars, upper, lower, digit) |
| User enumeration | `/forgot-password` always returns 200 |
| Webhook forgery | Stripe HMAC verified when secret set |
| Double-booking | Mongo partial unique index |
| Self-revoke / lockout | Super-admins blocked from demoting/revoking self or last super-admin |
| Inactive admin login | Blocked at `require_user` |
| Mongo `_id` leakage | All `find()` use `{"_id": 0}` projection |
| CORS | `CORS_ORIGINS` env var, set to real domain in prod |
| JWT secret | Required (no default fallback) — fails fast if missing |

## Performance posture

- All DB I/O async via Motor
- bcrypt + Resend + Stripe sync calls wrapped in `asyncio.to_thread`
- 22 indexes including unique constraints on hot writes
- Token verification: O(1) SHA-256 fp lookup → bcrypt verify single candidate
- Response payloads: slim DTOs, no full doc dumps
- TTL collections auto-clean (no orphaned rows)

## Known limitations / planned

- `Admin.jsx` is 1,136 lines — split planned
- No API versioning (`/api/v1/`) yet
- No idempotency keys on POST `/bookings/confirm`
- Hardcoded `POPULAR_PAIRS` and home tile copy — to be replaced with real route config
- Single-tenant Stripe — Stripe Connect needed for partner commission split
- No partner-API namespace yet (parked until first partner deal)
