# Loremaster engine protocol v1

The worker also publishes optional `snapshot.progression`: bounded XP-gain
history, session XP/AA counts and rates, current-level percent when known, and
an evidence-qualified ETA. `engine.xp-checkpoint` accepts numeric `level`
(integer 1–125) and `percent` (0 inclusive to 100 exclusive). Two checkpoints
at the same level at least 60 seconds apart can establish a pace when the log
omits quantities; that pace expires after 30 minutes. Level changes and deaths
invalidate the previous pace. Older snapshots without this field remain valid.

Loremaster's Python parser remains the source of truth. The Electron desktop
consumes immutable snapshots defined in
`loremaster/engine_protocol.py` and mirrored by
`loremaster-desktop/src/protocol.ts`.

## Transport

The production worker transport is UTF-8 JSON Lines over supervised local
standard I/O. Version 1 requires no network listener, browser permissions,
shared memory, or process injection. Checked-in envelopes retain the exact
same shape for deterministic renderer tests.

Each line is one envelope:

```json
{
  "protocolVersion": 1,
  "sequence": 42,
  "occurredAt": "2026-08-06T20:00:04.000Z",
  "eventType": "engine.snapshot",
  "snapshot": {}
}
```

`sequence` increases monotonically per worker process. A renderer must reject
unknown protocol versions and stale/out-of-order sequences. Snapshots replace
renderer state atomically; they are not mutable patches.

## Snapshot contract

- `character`: name, level, exact configured composition, and zone.
- `combat`: active state, encounter name, fight/session DPS, and separate
  personal, charmed-pet, and summoned-pet damage.
- optional `encounters`: bounded fight history with contributor totals and
  optional per-actor `sources` for recorded ability damage, hits, maximum hit,
  and evidence category. Each actor emits at most the 24 highest-damage sources;
  omitted source rows never reduce that actor's authoritative damage total.
  Older actors without `sources` remain valid and render without invented
  ability detail. Group damage remains separate from personal totals.
- `controls`: urgency-ordered mez/lull rows.
- `hiddenControlRows`: compact overflow count.
- `controlNoticeCount`: visible honest-unknown/failure notices.
- `controlAmbiguityCount`: cumulative ambiguous result observations.
- optional `alerts`: short-lived, deduplicated danger events such as a proven
  charm break.
- optional `weekly`: six non-Sky raid targets × D0–D4 lockouts for the current
  Tuesday 8:00 AM Pacific reset period; ordinary trash is never promoted and
  the selected difficulty is never guessed.

Every control row includes:

- `kind`: `mez` or `lull`.
- `state`: `active`, `unconfirmed`, `ambiguous`, or `failed`.
- target/count and spell/rank identity.
- landing, guaranteed-safe, and conservative expiry timestamps.
- safe and total remaining seconds.
- `lastTick` plus safe/warning/critical urgency.
- `confidence`: confirmed/exact/conservative/unconfirmed.
- an explicit ambiguity explanation where relevant.

An `active` state is impossible without sufficient landing evidence. A silent
spell such as Harmony is represented as `unconfirmed` and has no countdown.

## Renderer rules

1. Never derive control success from cast events or interpolate a missing
   target.
2. Use safe remaining time as the headline countdown.
3. Show last-tick, warning, failure, and ambiguity with text/symbols as well as
   color.
4. Coalesce snapshots before render; never block on filesystem, wiki, OCR, or
   parser work.
5. Preserve the last valid snapshot if a malformed or incompatible envelope
   arrives and surface protocol health separately.

## Compatibility

Version 1 is append-only within optional fields. Removing or changing the
meaning of a required field increments `protocolVersion`. Python serialization
and TypeScript fixture validation are both regression-tested.
