# Doppler variable manifest

All secrets live in Doppler, project `finding-memeland`, never in the repo.
Local dev: `doppler run -- python -m finding_memeland.main`.
Prod: Doppler ↔ Railway integration injects these automatically (checklist step 32).

## Configs

- `dev` — local development
- `prd` — Railway production

## Variables

| Variable | Type | Notes |
|---|---|---|
| `FMML_ENV` | config | `local` / `production` |
| `LOG_LEVEL` | config | default `INFO` |
| `ANTHROPIC_API_KEY` | secret | clue + persona generation |
| `ANTHROPIC_MODEL` | config | e.g. `claude-sonnet-4-6` |
| `OPENAI_API_KEY` | secret | avatar image generation (gpt-image) |
| `OPENAI_IMAGE_MODEL` | config | `gpt-image-1` / `gpt-image-1.5` |
| `OPENAI_IMAGE_SIZE` | config | e.g. `1024x1024` |
| `SUPABASE_URL` | secret | project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | secret | server-side key — never client |
| `X_API_KEY` | secret | single dev app |
| `X_API_SECRET` | secret | |
| `X_BEARER_TOKEN` | secret | app-only reads |
| `X_MAIN_ACCESS_TOKEN` | secret | @FindingMemeland OAuth |
| `X_MAIN_ACCESS_SECRET` | secret | |
| `X_PERSONA_<id>_ACCESS_TOKEN` | secret | one pair per authorized persona |
| `X_PERSONA_<id>_ACCESS_SECRET` | secret | added at OAuth time (step 16) |
| `BASE_RPC_URL` | config | Base mainnet RPC |
| `FMML_TOKEN_ADDRESS` | config | set after Clanker deploy (step 20) |
| `HOT_WALLET_PRIVATE_KEY` | secret | EOA; cap exposure to 1–2 hunts |
| `PAYOUT_CAP_FMML` | config | hardcoded per-hunt ceiling |
| `TELEGRAM_BOT_TOKEN` | secret | |
| `TELEGRAM_ADMIN_CHAT_ID` | config | hardcoded allowlist of one |
| `INTEGRITY_SALT` | secret | revealed per hunt in Winner Announcement |
| `PRIZE_USD_MIN` / `PRIZE_USD_MAX` | config | dollar-denominated prize band |
| `HOLDING_FLOOR_FMML` | config | STANDING public eligibility floor in tokens (launch: 50M); only ever reduce, announce reductions (0 = USD fallback) |
| `HOLDING_FLOOR_USD` | config | eligibility floor fallback in USD (converted at trigger) |

## Rules

- Service role key, hot wallet key and persona tokens are **secrets** — rotate on any leak.
- `FMML_TOKEN_ADDRESS` stays empty until the token is deployed. The agent refuses
  to start a hunt with an empty token address.
- `INTEGRITY_SALT` must be high-entropy and is the same across a hunt; it is
  published only after that hunt resolves.
