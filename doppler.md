# Doppler variable manifest

All secrets live in Doppler, project `finding-memeland`, never in the repo.
Local dev: copy `.env.example` to `.env` (git-ignored) or `doppler run -- PYTHONPATH=src python -m finding_memeland.main`.
Production: the Doppler ↔ Railway integration injects config **`prd`**; a change in Doppler restarts the worker.

## Configs

- `dev` — local development
- `prd` — Railway production. **Check the config selector before saving** — a value saved in `dev` by mistake is invisible to production.

Several names keep the historical `FMML_` prefix. The token is $FIND.

## Variables

### Runtime & AI

| Variable | Type | Notes |
|---|---|---|
| `FMML_ENV` | config | `local` / `production` |
| `LOG_LEVEL` | config | default `INFO` |
| `ANTHROPIC_API_KEY` | secret | relic identities, clues, oracle replies |
| `ANTHROPIC_MODEL` | config | e.g. `claude-sonnet-4-6` |
| `OPENAI_API_KEY` | secret | relic artwork; also the default blind solver |
| `OPENAI_IMAGE_MODEL` | config | `gpt-image-1` |
| `OPENAI_IMAGE_SIZE` | config | e.g. `1024x1024` |
| `RELIC_SOLVER_BACKEND` | config | `openai` (default) / `anthropic` / `off` — the model that tries to solve each puzzle clue before it publishes; keep it different from the writer |
| `RELIC_SOLVER_MODEL` | config | e.g. `gpt-4.1-mini` |

### Data & channels

| Variable | Type | Notes |
|---|---|---|
| `SUPABASE_URL` | secret | project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | secret | server-side key — never client |
| `X_API_KEY` / `X_API_SECRET` | secret | single dev app |
| `X_BEARER_TOKEN` | secret | app-only reads (claim thread, reshare checks) |
| `X_MAIN_ACCESS_TOKEN` / `X_MAIN_ACCESS_SECRET` | secret | @findingmemeland OAuth 1.0a — publishes clues and replies |
| `TELEGRAM_BOT_TOKEN` | secret | operator console |
| `TELEGRAM_ADMIN_CHAT_ID` | config | hardcoded allowlist of one chat |

### Money path

| Variable | Type | Notes |
|---|---|---|
| `BASE_RPC_URL` | config | Base mainnet RPC (a dedicated provider endpoint in prod) |
| `FMML_TOKEN_ADDRESS` | config | $FIND token contract |
| `HOT_WALLET_PRIVATE_KEY` | secret | pays prizes; holds at most a couple of hunts' worth |
| `PAYOUT_CAP_FMML` | config | hardcoded per-hunt ceiling in tokens; `0` refuses hunts |
| `INTEGRITY_SALT` | secret | high-entropy; part of every hunt's commitment, revealed per hunt in the Winner Announcement |

### Relic hunts

| Variable | Type | Notes |
|---|---|---|
| `RELIC_LAUNCH` | config | `true` in production — `/launch` stages a relic hunt |
| `RELIC_POOL_KEY` | secret | Fernet key encrypting every relic identity. **Losing it loses the pool** — there is no plaintext copy by design. Back it up before the first `/relic_new` |
| `RELIC_WALLET_REFS` | config | comma-separated refs of mint wallets available to `/relic_mint`, e.g. `RX05`. One wallet mints exactly one relic; after a mint the ref is spent |
| `<REF>_ADDR` / `<REF>_PK` | secret | the mint wallet behind each ref (`RX05_ADDR`, `RX05_PK`, …). `trocar_carteira.py` generates a new pair and prints the values to set |
| `PINATA_JWT` | secret | pins relic metadata + artwork to IPFS at mint |
| `RELIC_MINT_BACKEND` | config | `manifold` (default) / `relicnft` |
| `MANIFOLD_IMPLEMENTATION` | config | leave empty (address recorded in the artifact); override requires `MANIFOLD_IMPLEMENTATION_OVERRIDE_OK=true` |
| `RARIBLE_API_KEY` / `OPENSEA_API_KEY` | secret | findability gate at `/launch`; at least one — without a confirmed hit the launch is refused |
| `RELIC_TRAILS_ENABLED` | config | verified trail clues; `false` = direct clues |

### Game parameters

| Variable | Type | Notes |
|---|---|---|
| `MIN_PRIZE_FMML` | config | minimum pot `/launch` accepts (tokens) |
| `NON_HOLDER_PRIZE_PCT` | config | non-holder winner's share, % (holders take 100) |
| `HOLDING_FLOOR_FMML` | config | standing public holding floor in tokens; only ever reduce, announce every reduction |
| `HOLDING_HOURS` | config | continuous-hold window for the full pot; `0` = balance at claim time only |
| `CLUE_MIN_GAP_S` / `CLUE_MAX_GAP_S` | config | clue cadence band in seconds; `/status` shows the worst-case hunt length against the holding window |
| `CLAIM_CHANNEL` | config | `post` (public replies on Clue 1) |
| `CLAIM_GUESS_CAP` | config | code-like guesses per account per hunt |
| `WALLET_TIMEOUT_S` | config | winner's window to post a wallet, from the public ask |
| `CLAIM_SWEEP_EVERY_N` | config | full-thread sweep cadence (backstop for replies the mentions timeline misses) |
| `WATCHDOG_STALL_S` | config | Telegram alert if a live hunt's loop stalls this long |
| `FILLER_DAILY_ENABLED` / `FILLER_HOUR_UTC` | config | daily oracle post drafts for approval |

### Legacy (persona era)

`PREP_WINDOW_H`, `PREP_POSTS_N`, `MIN_WARMUP_DAYS`, `PERSONA_REGISTER`, `PRIZE_USD_MIN`, `PRIZE_USD_MAX`, `MIN_PRIZE_USD`, `FMML_USD_PRICE`, `HOLDING_FLOOR_USD`, `TOTAL_SUPPLY`, `X_PERSONA_<id>_*` — read by the old persona code paths only; a relic hunt never touches them.

## Rules

- Service role key, hot wallet key, relic pool key and mint wallet keys are **secrets** — rotate on any leak.
- **Never delete** `RELIC_WALLET_REFS` or any `RX*_*` / `RW*_*` pair: a spent mint wallet still holds the relic until the trophy transfer, and its key is what signs that transfer.
- `RELIC_WALLET_REFS` is a deliberate allowlist: it names only the wallet(s) available to mint next, never the whole key set.
- `INTEGRITY_SALT` is the same across a hunt and is published only after that hunt resolves.
- Mint wallets are never funded from the hot wallet or the treasury.
