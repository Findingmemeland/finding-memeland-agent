# Finding Memeland — Agent

The autonomous agent that runs the [Finding Memeland](https://findingmemeland.com) treasure hunt on Base: it invents a fictional being, mints it as a 1/1 NFT, writes and publishes the clues, reads the public claims, proves the winner's holdings on-chain, pays the prize in **$FIND** and hands the NFT over as the trophy. No human approves any game action.

> **Status:** in production. Hunts run live from [@findingmemeland](https://x.com/findingmemeland) on the mechanic described below (relic hunts since Hunt #7, August 2026). The design document is the litepaper (v0.8) on the site; where this README and the litepaper disagree on a game parameter, the litepaper is the public commitment.

Licence: MIT.

## How a hunt works

**1. The relic.** The agent generates a two-word name, a fragment of lore and an image prompt, renders the artwork, and stores the identity **encrypted** (Fernet; there is no plaintext column, and losing the key loses the pool — that is the point). A separate pipeline mints it blind on Base as a 1/1 NFT: metadata pinned to IPFS, the hunt's **claim code** appended to the on-chain description, ownership renounced right after the mint. From that block on nothing about the relic can change — by anyone, the agent included. Each relic plays exactly one hunt; the mint is deliberately unremarkable, one more NFT among the thousands created on Base every day.

**2. Clue 1 — the opening post.** Published on the main X account. It announces the hunt and the pot, carries the first puzzle piece, is the **reshare gate** (your account must have reposted or quoted this exact post to be eligible), is the **claim window** (every claim is a public reply to it), and carries the **integrity hash** — see below.

**3. Clues.** The opening phase is seven hard *puzzle pieces*: each is one oblique constraint on the name or the artwork, from an assigned angle (semantic field, cultural use, structure, relation between the two words, …), never the same angle twice for a word, never a rhyme, never an emoji. Before a piece publishes it must pass deterministic guardrails (no literal leak — emoji are read by their Unicode names) **and defeat a blind AI solver**: a different model from the writer, shown only the clue (and, early on, the clues before it), that must fail to guess the answer. A piece that can be solved alone is rejected and rewritten; difficulty is enforced by proof, not by prompt. After the puzzle phase the clues ease until someone wins. Cadence is random within a configured band. A hunt unsolved after 72 hours is publicly voided.

**4. Claims — public, on the thread.** A hunter decodes the name, searches it on an NFT marketplace, reads the code off the relic's description and replies to Clue 1 with it. Ordering is the reply's own timestamp (post id as a millisecond tiebreak), so the race is auditable by anyone and copying a code out of someone else's reply can never beat them. The reshare gate is eliminatory and checked at processing time ("claim invalid — missing repost" → repost and post the code again). Wrong codes get one public jeer per account from the oracle; after five code-like guesses an account is ignored for the rest of the hunt. The jeer engine is *architecturally* unable to leak: it never receives the answer or any clue, only the player's text and a banned-terms list used to validate its own output.

**5. Payout and trophy.** The winner is asked, on the thread, for a Base wallet — accepted only from the same account id, within ten minutes. The agent then proves holder status directly from the chain by replaying every `Transfer` event that touched the wallet inside the holding window: a wallet that held the public floor continuously takes **100% of the announced pot; a non-holder takes 10%** (the floor and the window are the litepaper's numbers, stated in every Clue 1). The prize is paid in $FIND from a capped hot wallet, the relic NFT is transferred to the winner, and the Winner Announcement names the relic, links its marketplace page, shows the payout transaction and reveals the ingredients of the integrity hash.

## Provable integrity

```
integrity_hash = SHA-256(relic_id + claim_code + secret_salt)
relic_id       = chain:contract:tokenId        # e.g. base:0x…:1
```

The hash is published inside Clue 1, before any other clue exists. The Winner Announcement reveals `relic_id`, the claim code and the salt, so anyone can recompute it. Because the relic's contract ownership is renounced at mint, "the target never moved" is a property of the chain, not a promise.

**Operational blindness** is built into the tooling, not into a policy: identities are stored encrypted, `/relic_new` and `/relic_mint` never print a name, artwork or code, the launch confirmation is bound to a relic *id*, and every game post publishes without approval. The operator decides *when* a hunt fires and holds a kill switch (`/silence`); pauses are disclosed publicly. The operator and associates are ineligible to win.

## Architecture

```
src/finding_memeland/
├── main.py                     # composition root: builds every client and wires the Orchestrator
├── config.py                   # pydantic settings, env-driven (Doppler in prod)
├── preflight.py                # pre-hunt checks: AI services, X API, RPC, gas and prize balance
├── persona/
│   ├── relic_generator.py      # LLM identity: two-word name, lore, image prompt, solution terms
│   ├── relic_pool.py           # encrypted pool of relic identities (Fernet at rest)
│   ├── relic_mint.py           # blind mint on Base: IPFS pin, code in description, renounce
│   ├── relic_wallets.py        # one fresh mint wallet per relic, keys resolved by ref at signing
│   ├── relic_findability.py    # launch gate: is the relic indexed by name on a marketplace?
│   └── relic_integration.py    # trophy hand-off after payout
├── content/
│   ├── relic_clues.py          # puzzle-piece plan, angle assignment, phase rules, blind solver
│   ├── relic_trail.py          # verified "trail" clues (optional, off by default)
│   ├── clue_engine.py          # clue loop, cadence, easing curve
│   ├── guardrails.py           # deterministic leak checks (emoji expanded to names)
│   ├── integrity.py            # SHA-256 commitment
│   └── templates.py            # frozen post templates (Clue 1, reveal, jeers)
├── claims/
│   ├── source.py               # reads the Clue 1 thread via the X mentions timeline + sweeps
│   ├── parser.py               # candidate codes, wallets, code-like vs chatter
│   └── taunts.py               # the oracle's jeers — receives no game content by construction
├── chain/
│   ├── holdings.py             # Transfer-event replay: exact holding proof on Base
│   ├── payout.py               # $FIND transfer from the capped hot wallet
│   └── relic_trophy.py         # NFT transfer to the winner, receipt verified on-chain
├── orchestrator/state_machine.py   # deterministic hunt lifecycle, crash-resume from persisted state
├── social/                     # X client, publisher, text sanitiser
├── telegram/                   # operator console: /launch, /status, /relic_new, /relic_mint, …
├── db/client.py                # Supabase repositories (hunts, relics, submissions)
└── supervisor/watchdog.py      # anomaly detection + kill switch
contracts/                      # the two mint backends (Solidity + compiled artifacts)
db/schema.sql                   # Supabase schema
tests/                          # ~560 tests: pytest -q
```

The persona-era modules (`dm/`, `persona/dresser.py`, `persona/generator.py`, `persona/dress_pipeline.py`) are the previous mechanic — a disposable X account as the hidden target, claims by DM. They are kept for history and for the persona code paths still covered by tests; a relic hunt never touches them. Internal env names still carry the historical `FMML_` prefix; the token is $FIND.

## Hunt lifecycle

```
idle → preparing → live → resolving → paying → pending_cleanup → retiring → done
                        ↘ voided (72h unsolved, or aborted) → retiring
```

Every step persists its state. A restart mid-hunt resumes with the same relic, claim code, reshare gate and submission marker; the pending winner, the ten-minute wallet window and the claim queue survive a restart too.

## Operating it

All operator actions go through a Telegram bot restricted to one admin chat.

```
/relic_new [1-10]     create relic identities (encrypted; prints ids, never names)
/relic_mint [id]      mint the oldest un-minted relic — one per call, spends gas, burns one mint wallet
/launch <pot>         e.g. /launch 500M · /launch 1B — stages the hunt, runs the findability gate,
                      asks for confirmation bound to the relic id, then publishes Clue 1
/status               hunt state, cadence, floor, pool
/silence · /resume    kill switch: pause / resume the live hunt (disclosed publicly)
```

Minting needs one fresh wallet per relic (`RELIC_WALLET_REFS`, keys as `<REF>_ADDR` / `<REF>_PK` in Doppler) and a Pinata JWT. Launching needs a marketplace API key for the findability gate. `trocar_carteira.py` rotates the mint wallet between relics.

## Setup

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # local only — production reads Doppler
PYTHONPATH=src python -m pytest -q
PYTHONPATH=src python -m finding_memeland.main
```

Requires Python 3.11+. Create the Supabase project from `db/schema.sql`; set the variables listed in `doppler.md` (project `finding-memeland`, configs `dev` / `prd`). The agent refuses to run a hunt until the money-path settings (token address, hot wallet, payout cap, integrity salt) and — with `RELIC_LAUNCH=true` — the relic pool key are set.

## Deploy

`Procfile` runs the worker. On Railway, deploy from this repo with the Doppler ↔ Railway integration (config `prd`) injecting secrets; no CLI needed in production. The process logs which blind-solver backend it booted with (`[relic] blind solver: …`).

## Security

- No secret lives in this repo. Everything comes from Doppler at runtime; `.env` is git-ignored.
- The hot wallet holds at most a couple of hunts' worth of prizes, with a hardcoded per-hunt cap. The prize treasury is a separate Safe the agent cannot touch.
- Relic identities are encrypted at rest; the mint pipeline and the operator console never display them.
- Mint wallets are used once. Their keys are resolved by reference at the instant of signing and are never logged.
- The jeer engine cannot leak a clue by construction; the clue engine cannot publish a piece that fails the guardrails or that the blind solver can answer.

## Verifying a past hunt

Take the Clue 1 post's `integrity:` value and the Winner Announcement's `relic_id`, `claim_code` and `salt`; `SHA-256(relic_id + claim_code + salt)` must equal it. The relic's contract on BaseScan shows the renounced ownership and the transfer to the winner.
