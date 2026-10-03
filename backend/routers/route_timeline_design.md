# Route & schedule timeline design

This document defines the target model for the next route/schedule improvement cycle.

## Core principle: single source of truth

The system should not store stop timing data in two places and let them drift apart.

### Keep this in the route layer
- route identity
- route origin/destination city
- ordered boarding stops
- ordered alighting stops
- allowed segment pairings and fare matrix
- route-level route code and metadata

### Keep this in the schedule layer
- actual trip date/time
- physical bus instance identity
- trip-specific departure time
- trip-specific arrival time
- per-trip segment chosen for a sold booking
- override values for one bus run when needed

## Why this matters

The current pattern creates a mismatch:
- route builder stores offsets per stop
- schedule builder asks for times again
- admins can accidentally set different values in two places

This makes route timing fragile and makes validation harder.

## Proposed model

### Route model
```json
{
  "id": "route_xxx",
  "code": "KLSG",
  "name": "KL → Singapore",
  "origin_city": "Kuala Lumpur",
  "destination_city": "Singapore",
  "boarding_stops": [
    { "terminal_id": "t1", "sequence": 1 },
    { "terminal_id": "t2", "sequence": 2 }
  ],
  "alighting_stops": [
    { "terminal_id": "t3", "sequence": 1 },
    { "terminal_id": "t4", "sequence": 2 }
  ],
  "pairings": [
    { "pickup_id": "t1", "dropoff_id": "t3", "adult_fare": 55 }
  ]
}
```

### Schedule model
```json
{
  "id": "schedule_yyy",
  "route_id": "route_xxx",
  "from_terminal_id": "t1",
  "to_terminal_id": "t3",
  "departure_date": "2026-10-12",
  "departure_time": "08:00",
  "arrival_time": "13:30",
  "bus_instance_id": "bus_abc",
  "days_of_week": [1, 3, 5]
}
```

## Timeline behavior

The schedule should calculate actual stop times from the route sequence and trip departure time, not ask the admin to enter duplicate route timings in two forms.

### Example
Route sequence:
- KL Sentral
- Melaka Sentral
- JB Sentral
- Singapore

Schedule:
- departure = 08:00
- admin edits only `trip departure` and `leg duration` values when necessary
- system derives ETAs for stop order and segment selection

## Validation rules

Before saving:
- all boarding stop codes must resolve to real terminals
- all alighting stop codes must resolve to real terminals
- route pairings must only reference valid pickup/dropoff stop IDs
- each schedule must pick a valid route segment
- no dropoff may appear before its pickup in the route path

## Recommended UI behavior

In the admin dashboard:
- show one route timeline, not two separate manual editors
- keep the visual route and the trip schedule aligned
- allow per-trip override only on the schedule editor, not on the route template

This reduces admin confusion and prevents contradictory timing data.

## Migration strategy

1. keep existing `offset_min` data temporarily
2. add route timeline validation in admin UI
3. update schedule creation to use route ordering as the default source
4. remove duplicate time-entry workflow only after UI migration is complete
5. retain schema compatibility with old data until migrated

## Outcome

This creates a clean, predictable admin flow:
- build route network once
- create journeys once
- override actual trip timing only at schedule creation time when needed
