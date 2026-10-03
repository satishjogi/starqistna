# Backend module plan

This directory is the staging ground for the backend modularization.

## Purpose

The long-term target is to replace the single `backend/server.py` monolith with a router-based structure in `backend/routers/`, while keeping the API stable for the frontend and deployment environment.

## Current state

This branch introduces the router skeletons without breaking the current backend runtime.

Planned modules:

- `routes.py` — route templates, pairings, bus type metadata
- `schedules.py` — schedule creation, bulk creation, bulk delete, schedule edits
- future additions: `auth.py`, `bookings.py`, `payments.py`, `admin.py`, `feedback.py`, `terminals.py`

## Why this is safe

- no `.env` keys are touched
- no deployment config changes are required
- public API endpoints stay stable while the code splits apart
- frontend can continue calling `/api/...` routes without changes

## Next step

The next move is to move the route/schedule logic out of `backend/server.py` and keep the route manager and schedule manager in their own modules before refining the route timeline behavior.
