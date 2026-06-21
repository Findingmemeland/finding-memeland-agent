-- Finding Memeland — Supabase schema
-- Run in the Supabase SQL editor after creating the project (checklist step 18).
-- Postgres. All timestamps are timestamptz (UTC).

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------

-- Persona pipeline lifecycle (checklist step 18 / design: conveyor belt).
create type persona_state as enum (
  'created',     -- account exists, @ fixed, not yet warmed
  'warmup',      -- ~10-day light organic activity
  'ready',       -- warmed, OAuth-authorized, available for a hunt
  'in_play',     -- currently dressed and running a hunt
  'retired'      -- used once, dormant; deleted after 30 days
);

-- Hunt lifecycle — mirrors the orchestrator state machine.
create type hunt_state as enum (
  'idle',
  'preparing',
  'live',
  'resolving',
  'paying',
  'pending_cleanup',   -- 1h reveal window
  'retiring',
  'done',
  'voided'             -- platform interruption / abort
);

-- Outcome of a single DM submission.
create type submission_outcome as enum (
  'pending',
  'won',
  'bad_code',
  'no_holding',
  'no_reshare',
  'bot_disqualified',
  'late',              -- valid but a winner already existed
  'malformed'          -- no parseable wallet address
);

-- ---------------------------------------------------------------------------
-- Personas — the pipeline of single-use accounts
-- ---------------------------------------------------------------------------
create table personas (
  id             uuid primary key default gen_random_uuid(),
  handle         text not null unique,        -- neutral @, NEVER changes
  x_user_id      text unique,                 -- permanent X numeric id
  state          persona_state not null default 'created',
  -- OAuth tokens are NOT stored here in plaintext; kept in Doppler keyed by id.
  oauth_ref      text,                         -- Doppler key suffix, e.g. '01'
  esim_ref       text,                         -- bookkeeping only
  warmup_started_at timestamptz,
  ready_at       timestamptz,
  used_in_hunt   bigint,                       -- fk hunts.id (one hunt only)
  retired_at     timestamptz,
  delete_after   timestamptz,                  -- retired_at + 30 days
  created_at     timestamptz not null default now()
);
create index personas_state_idx on personas (state);

-- ---------------------------------------------------------------------------
-- Hunts
-- ---------------------------------------------------------------------------
create table hunts (
  id             bigint generated always as identity primary key,
  state          hunt_state not null default 'idle',
  persona_id     uuid references personas (id),
  -- Identity applied to the persona for this hunt (audit copy).
  persona_display_name text,
  persona_bio    text,
  -- Integrity protocol. salt is secret until reveal; hash is published in Clue 1.
  claim_code     text not null,
  integrity_salt text not null,                -- revealed in Winner Announcement
  integrity_hash text not null,                -- SHA-256(x_user_id+claim_code+salt)
  -- Economics, resolved at trigger time.
  prize_usd      numeric(12,2),
  prize_fmml     numeric(38,0),                -- token base units
  fmml_usd_price numeric(38,18),               -- price used for conversion
  -- Eligibility parameters for this hunt (Genesis ramp).
  min_balance_fmml   numeric(38,0),
  holding_hours      integer not null,         -- 48 / 96 / 168...
  reshare_post_id    text,                     -- Clue 1 tweet id = reshare gate
  -- Outcome.
  winner_submission_id bigint,
  started_at     timestamptz,
  resolved_at    timestamptz,
  cleanup_due_at timestamptz,                  -- resolved_at + 1h
  created_at     timestamptz not null default now()
);
create index hunts_state_idx on hunts (state);

alter table personas
  add constraint personas_used_in_hunt_fk
  foreign key (used_in_hunt) references hunts (id);

-- ---------------------------------------------------------------------------
-- Clue history — one row per clue, generated one at a time
-- ---------------------------------------------------------------------------
create table clues_history (
  id            bigint generated always as identity primary key,
  hunt_id       bigint not null references hunts (id) on delete cascade,
  clue_index    integer not null,             -- 1 = opening post
  clue_text     text not null,
  tweet_id      text,                          -- X post id once published
  obliqueness   numeric(4,3),                  -- 1.0 = max oblique, eases down
  posted_at     timestamptz,
  next_due_at   timestamptz,                   -- scheduled T+random(1h,3h)
  created_at    timestamptz not null default now(),
  unique (hunt_id, clue_index)
);

-- ---------------------------------------------------------------------------
-- Submissions — full audit log, published after each hunt
-- ---------------------------------------------------------------------------
create table submissions (
  id            bigint generated always as identity primary key,
  hunt_id       bigint not null references hunts (id),
  dm_id         text unique,                   -- X DM id (dedup)
  sender_x_id   text not null,
  wallet        text,                          -- parsed 0x address
  submitted_claim_code text,
  outcome       submission_outcome not null default 'pending',
  -- Per-filter results for transparency / debugging.
  check_code    boolean,
  check_holding boolean,
  check_reshare boolean,
  check_bot     boolean,
  bot_reason    text,
  x_created_at  timestamptz,                   -- arrival order, ms precision
  processed_at  timestamptz not null default now()
);
create index submissions_hunt_idx on submissions (hunt_id);
create index submissions_order_idx on submissions (hunt_id, x_created_at);

-- ---------------------------------------------------------------------------
-- Winners
-- ---------------------------------------------------------------------------
create table winners (
  id            bigint generated always as identity primary key,
  hunt_id       bigint not null unique references hunts (id),
  submission_id bigint references submissions (id),  -- nullable: audit lives in submissions
  winner_x_id   text not null,
  wallet        text not null,
  prize_fmml    numeric(38,0) not null,
  payout_id     bigint,                         -- fk payouts.id
  created_at    timestamptz not null default now()
);

-- ---------------------------------------------------------------------------
-- Payouts — on-chain prize transfers (the public ledger mirror)
-- ---------------------------------------------------------------------------
create table payouts (
  id            bigint generated always as identity primary key,
  hunt_id       bigint not null references hunts (id),
  wallet        text not null,
  amount_fmml   numeric(38,0) not null,
  tx_hash       text,
  status        text not null default 'pending', -- pending|sent|confirmed|failed
  error         text,
  created_at    timestamptz not null default now(),
  confirmed_at  timestamptz
);

alter table winners
  add constraint winners_payout_fk
  foreign key (payout_id) references payouts (id);

-- ---------------------------------------------------------------------------
-- Holding samples — daily on-chain balance snapshots for continuity checks
-- ---------------------------------------------------------------------------
create table holding_samples (
  id            bigint generated always as identity primary key,
  wallet        text not null,
  balance_fmml  numeric(38,0) not null,
  sampled_at    timestamptz not null default now()
);
create index holding_samples_wallet_idx on holding_samples (wallet, sampled_at);

-- ---------------------------------------------------------------------------
-- Approval queue — non-game posts awaiting Telegram approval
-- ---------------------------------------------------------------------------
create table approval_queue (
  id            bigint generated always as identity primary key,
  kind          text not null,                  -- pinned_rules | filler | comms
  draft_text    text not null,
  status        text not null default 'pending',-- pending|approved|rejected|edited
  telegram_msg_id text,
  decided_at    timestamptz,
  created_at    timestamptz not null default now()
);
