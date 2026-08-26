"""Entrypoint + composition root.

    doppler run -- python -m finding_memeland.main

`build_agent` is the single place that constructs the heavy clients (Anthropic,
OpenAI, web3, Supabase, X) and wires them, via the runtime adapters, into the
Orchestrator. Everything else in the codebase depends only on the ports, so this
is the one import-heavy module.

The agent boots idle. Hunts fire on the admin's Telegram /launch (the bot loop —
TelegramAdmin.build_application — is the final live wiring step). run_hunt()
fails fast via settings.assert_ready_for_hunt() if token/wallet aren't set
(prizes are token-denominated since 2026-07-31 — no price needed to launch).
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Settings, get_settings


@dataclass
class Agent:
    orchestrator: object
    telegram: object
    repo: object


def build_agent(settings: Settings | None = None) -> Agent:
    s = settings or get_settings()

    # Heavy clients (imported here so the rest of the codebase stays light).
    from anthropic import Anthropic
    from openai import OpenAI
    from web3 import Web3

    from .chain.holdings import Holdings
    from .chain.payout import PayoutEngine
    from .content.clue_engine import (
        ClueEngine,
        holding_window_covers_hunt,
        next_clue_due_factory,
        worst_case_hunt_hours,
    )
    from .claims.source import XClaimSource
    from .claims.taunts import TauntEngine
    from .content.filler import FillerEngine
    from .content.persona_posts import PersonaPostEngine
    from .db.client import Repo, make_client
    from .dm.listener import XDMSource
    from .dm.validator import DMValidator
    from .orchestrator.state_machine import Orchestrator
    from .persona.avatar import AvatarGenerator
    from .persona.dress_pipeline import DressPipeline
    from .persona.dresser import PersonaDresser
    from .persona.generator import PersonaGenerator
    from .persona.source import (
        DBPersonaSource,
        _waiting_line,
        split_dressed_by_findability,
    )
    from .persona.verification import DressedProfileVerifier
    from .runtime import (
        DBHuntPause,
        fmt_tokens,
        parse_token_amount,
        ManualPriceFeed,
        PollHeartbeat,
        StdoutNotifier,
        SystemClock,
        TelegramNotifier,
        active_hunt_guard,
        env_token_resolver,
        hunt_status_line,
        write_temp_png,
    )
    from .preflight import preflight_check, preflight_money
    from .social.publisher import XPublisher
    from .social.x_client import XClient
    from .telegram.approval_queue import ApprovalQueue, TelegramAdmin
    from .telegram.confirmation import LaunchConfirmation

    # Hunt events (LIVE, winner, errors) go to the admin's Telegram, not stdout —
    # an autonomous agent that moves money must never run blind.
    notifier = (
        TelegramNotifier(s.telegram_bot_token, s.telegram_admin_chat_id)
        if s.telegram_bot_token and s.telegram_admin_chat_id
        else StdoutNotifier()
    )
    anthropic = Anthropic(api_key=s.anthropic_api_key)
    openai = OpenAI(api_key=s.openai_api_key, max_retries=4, timeout=120.0)
    repo = Repo(make_client(s.supabase_url, s.supabase_service_role_key))
    # /silence // /resume kill switch — persisted on the hunt row (hunts.paused)
    # so it survives restarts and deploy overlaps. Post-mortem P3.7: the old
    # in-memory Event meant a paused hunt auto-resumed after a Railway restart.
    control = DBHuntPause(repo)

    # --- Relic: blind identity pool -------------------------------------
    # Built whenever a key exists, even with relic_launch=False: CREATING
    # relics is independent of LAUNCHING hunts, and tying both to one switch
    # would force relic mode on just to start filling the pool. Minting is
    # what starts a relic's anonymity clock, so the pool wants a head start.
    relic_pool = relic_generator = None
    if s.relic_pool_key:
        from .persona.relic_generator import RelicGenerator, name_words
        from .persona.relic_pool import FernetPoolCipher, RelicPool
        from .persona.relic_repo import SupabaseRelicRepo

        relic_pool = RelicPool(
            SupabaseRelicRepo(make_client(s.supabase_url, s.supabase_service_role_key)),
            FernetPoolCipher(s.relic_pool_key),
        )

        class _PoolOnlyNameCheck:
            """Until the OpenSea key lands there is no way to test whether a name
            already exists in the world, so we only enforce pool uniqueness.
            Deliberately permissive: a bad name is caught later by the launch
            gate, whereas a blocked creation produces nothing at all."""

            def __init__(self, pool):
                self._pool = pool

            def is_available(self, name: str) -> bool:
                return not (name_words(name) & self._pool.spent_words())

        relic_generator = RelicGenerator(
            anthropic, s.anthropic_model, _PoolOnlyNameCheck(relic_pool)
        )

    # Watchdog sensor: the hunt loop beats every cycle; a supervisor thread
    # below screams on Telegram if beats stop while a hunt is live (P0 pack).
    heartbeat = PollHeartbeat(stall_after_s=s.watchdog_stall_s)
    web3 = Web3(Web3.HTTPProvider(s.base_rpc_url))

    # --- Relic: minting ---------------------------------------------------
    # Separate from the pool on purpose: creating identities needs only a key,
    # while minting needs wallets, a pinning service and a compiled contract.
    # Each piece missing disables minting alone, and says which one is missing —
    # a half-configured mint must never half-run.
    relic_minter = relic_artwork = relic_wallets = None
    if relic_pool is not None and s.relic_wallet_ref_list and s.pinata_jwt:
        from .persona.relic_image import OpenAIRelicImage, PinataPinner, RelicArtwork
        from .persona.relic_repo import ConfigWalletDirectory, DopplerKeyResolver
        from .persona.relic_wallets import WalletPool

        relic_wallets = WalletPool(
            ConfigWalletDirectory(
                s.relic_wallet_ref_list,
                SupabaseRelicRepo(make_client(s.supabase_url, s.supabase_service_role_key)),
            ),
            DopplerKeyResolver(),
        )
        relic_artwork = RelicArtwork(
            OpenAIRelicImage(openai, model=s.openai_image_model, size=s.openai_image_size),
            PinataPinner(s.pinata_jwt),
        )
        try:
            from .persona.relic_mint import Web3Minter, load_contract_artifact

            _abi, _bytecode = load_contract_artifact()
            relic_minter = Web3Minter(
                web3=web3, wallets=relic_wallets, abi=_abi, bytecode=_bytecode
            )
        except Exception as e:  # noqa: BLE001 — no artifact == no minting, not a crash
            # Printed in full: the message lists every path that was tried, and
            # this is the only place that detail reaches the logs.
            print(f"[relic] minting disabled — {e}")
    # --- Relic: launching (block 3) ---------------------------------------
    # Only assembled with relic_launch on. The findability gate is FAIL-CLOSED
    # by design: no key, no launch. That is the correct failure — a relic that
    # cannot be found makes an unwinnable hunt, which is worse than no hunt.
    relic_findability = relic_findability_secondary = None
    relic_clue_engine = trophy_port = None
    if s.relic_launch and relic_pool is not None:
        from .chain.relic_trophy import Web3NFTTransfer
        from .content.relic_clues import RelicClueEngine
        from .persona.relic_findability import (
            BaseScanFindability, OpenSeaFindability, QuorumFindability,
            RaribleFindability,
        )

        def _http_get(url: str, headers: dict | None = None) -> str:
            import urllib.request

            req = urllib.request.Request(url, headers={
                # Marketplace APIs sit behind Cloudflare, which answers the
                # default urllib signature with a bare 403 (measured twice on
                # 2026-08-24/25). A browser UA is the whole fix.
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
                **(headers or {}),
            })
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read().decode("utf-8", "ignore")

        def _http_post(url: str, body: bytes, headers: dict) -> str:
            import urllib.request

            req = urllib.request.Request(url, data=body, headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
                ),
                **headers,
            }, method="POST")
            with urllib.request.urlopen(req, timeout=25) as r:
                return r.read().decode("utf-8", "ignore")

        # Quorum: two INDEPENDENT marketplaces must agree the relic is findable.
        # One marketplace hiding a fresh 1/1 is plausible; two agreeing is
        # evidence. Falls back to a single surface when only one key exists —
        # weaker, but a launch gated on one verified surface still beats no gate.
        _surfaces = [
            c for c in (
                OpenSeaFindability(http_get=_http_get, api_key=s.opensea_api_key)
                if s.opensea_api_key else None,
                RaribleFindability(http_post=_http_post, api_key=s.rarible_api_key)
                if s.rarible_api_key else None,
            ) if c is not None
        ]
        relic_findability = (
            QuorumFindability(tuple(_surfaces), required=2) if len(_surfaces) >= 2
            else (_surfaces[0] if _surfaces else None)
        )
        # BaseScan does NOT index NFT names (measured against a 17h-old control),
        # so it can only ever confirm the CONTRACT exists. Informational, never
        # a gate.
        relic_findability_secondary = (
            BaseScanFindability(http_get=lambda u: _http_get(u)),
        )

        trail_verifier = trail_policy = None
        if s.relic_trails_enabled:
            from .content.relic_trail import (
                AnthropicWebSearch, TrailPolicy, WebSearchTrailVerifier,
            )

            trail_verifier = WebSearchTrailVerifier(
                AnthropicWebSearch(anthropic, s.anthropic_model)
            )
            trail_policy = TrailPolicy(enabled=True)

        relic_clue_engine = RelicClueEngine(
            anthropic, s.anthropic_model,
            trail_verifier=trail_verifier, trail_policy=trail_policy,
        )
        if relic_wallets is not None:
            trophy_port = Web3NFTTransfer(web3=web3, wallets=relic_wallets)

    x = XClient(
        api_key=s.x_api_key, api_secret=s.x_api_secret, bearer_token=s.x_bearer_token,
        main_access_token=s.x_main_access_token, main_access_secret=s.x_main_access_secret,
    )

    holdings = Holdings(web3=web3, token_address=s.fmml_token_address, repo=repo)
    price_feed = ManualPriceFeed(s.fmml_usd_price)
    payout_engine = PayoutEngine(
        web3=web3, token_address=s.fmml_token_address,
        hot_wallet_key=s.hot_wallet_private_key, per_hunt_cap=int(s.payout_cap_fmml or 0),
    )
    hot_address = ""
    if s.hot_wallet_private_key:
        try:
            hot_address = web3.eth.account.from_key(s.hot_wallet_private_key).address
        except Exception:  # noqa: BLE001 — bad key surfaces in preflight/payout
            pass

    orchestrator = Orchestrator(
        settings=s,
        clock=SystemClock(),
        repo=repo,
        persona_source=DBPersonaSource(
            repo, env_token_resolver, min_warmup_days=s.min_warmup_days
        ),
        persona_generator=PersonaGenerator(anthropic, s.anthropic_model),
        avatar_generator=AvatarGenerator(
            openai, model=s.openai_image_model, size=s.openai_image_size
        ),
        dresser=PersonaDresser(x),
        publisher=XPublisher(x),
        clue_engine=ClueEngine(anthropic, s.anthropic_model),
        dm_source=XDMSource(x),
        validator=DMValidator(
            chain=holdings, x_client=x, profile_lookup=x.lookup_user,
            own_handles=["FindingMemeland"],
        ),
        payout=payout_engine,
        price_feed=price_feed,
        notifier=notifier,
        register=s.persona_register,
        holding_floor_usd=s.holding_floor_usd,
        holding_floor_fmml=int(getattr(s, "holding_floor_fmml", 0) or 0),
        holding_hours=s.holding_hours,
        avatar_writer=write_temp_png,
        # Clue cadence from config (defaults = the published 1-3h). Lets a hunt
        # be tightened (e.g. the Genesis hunt: 10-30min gaps -> a ~4h hunt)
        # without touching code. Built via the shared factory so the pre-flight
        # script verifies THIS code path, not a copy of it. See
        # Settings.clue_min_gap_s for the constraint tying this to holding_hours.
        clue_due_fn=next_clue_due_factory(s.clue_min_gap_s, s.clue_max_gap_s),
        control=control,
        heartbeat=heartbeat,
        # Relic mode. All None with relic_launch=false, which is exactly today's
        # persona behaviour — one env var reverts, no deploy.
        relic_launch=s.relic_launch,
        relic_pool=relic_pool,
        relic_findability=relic_findability,
        relic_findability_secondary=relic_findability_secondary or (),
        relic_clue_engine=relic_clue_engine,
        trophy_port=trophy_port,
        # Fase 3 (2026-08-13): the prep window is RETIRED from production —
        # pre-dressed personas arrive indexed with their anchor posts already
        # published at /dress time (the PersonaPostEngine now lives in the
        # DressPipeline). The window's code remains for simulation/live-test.
        # Claim-by-post (2026-07-25): submissions are public replies on the
        # Clue 1 post, read via mentions (the DM API only reads virgin
        # conversations). claim_channel='dm' reverts to the legacy DM loop.
        claim_source=XClaimSource(x) if s.claim_channel == "post" else None,
        taunt_engine=(
            TauntEngine(anthropic, s.anthropic_model)
            if s.claim_channel == "post" else None
        ),
        claim_guess_cap=s.claim_guess_cap,
        wallet_timeout_s=s.wallet_timeout_s,
        claim_sweep_every_n=s.claim_sweep_every_n,
        non_holder_prize_pct=s.non_holder_prize_pct,
        # Real hunts NEVER undress the persona: single-use accounts, and the
        # dressed profile stays up as the hunt's public artifact.
        undress_on_retire=False,
        # Pre-dressing (Fase 2, 2026-08-13): production launches consume the
        # DRESSED pool (oldest first) with fail-closed R3 verification. The old
        # generate-at-launch flow is retired here — no dressed persona means a
        # clean refusal, never a fallback to an unindexed account.
        predressed_launch=True,
        launch_verifier=DressedProfileVerifier(x),
    )

    # Admin/approval surface. /launch <prize in $FIND> fires a hunt in the BACKGROUND
    # (it can run for hours) so the bot stays responsive. The prize is set per hunt
    # by the operator, DIRECTLY in tokens since 2026-07-31 (/launch 500M) — no
    # USD conversion, no FMML_USD_PRICE required to launch.
    import threading

    # One hunt at a time: a double-tapped /launch must never start two hunts
    # (two personas, two prize payouts, one shared DM stream).
    hunt_flag = {"active": False}
    hunt_lock = threading.Lock()

    # Fase 3 (2026-08-13): /launch is INSTANT since Fase 2 (no prep window, no
    # take-backs), so it now STAGES the hunt and asks for an explicit sim/não —
    # the one protection that replaced the old 24h window of regret. Only the
    # confirmation fires _do_launch.
    launch_confirm = LaunchConfirmation()

    def _launch(arg: str) -> str:
        if not arg:
            return "usage: /launch <prize in $FIND>, e.g. /launch 500M or /launch 1B"
        try:
            prize_fmml = parse_token_amount(arg)
        except ValueError:
            return (
                f"'{arg}' isn't a token amount. usage: /launch <prize in $FIND> "
                "— e.g. /launch 500M, /launch 1B, /launch 500000000"
            )
        if prize_fmml < s.min_prize_fmml:
            return (
                f"minimum prize is {fmt_tokens(s.min_prize_fmml)} $FIND — "
                "nobody plays for less."
            )
        # Cheap early refusals BEFORE staging: an active hunt or an empty pool
        # would only fail later — say it now, with nothing staged.
        refusal = active_hunt_guard(repo)
        if refusal:
            return refusal

        # Relic mode branches BEFORE the persona pool is read: there are no
        # dressed personas in this world, so the checks below would refuse a
        # perfectly launchable hunt.
        if s.relic_launch:
            if relic_pool is None or relic_findability is None:
                return (
                    "⛔ relic_launch está ON mas falta configuração "
                    "(relic_pool_key / uma chave de marketplace). Nada lançado."
                )
            from .persona.relic_findability import FindabilityRefused
            from .telegram.relic_launch import stage_relic_launch

            try:
                summary, prompt, relic, _identity = stage_relic_launch(
                    pool=relic_pool,
                    prize_fmml=prize_fmml,
                    ladder_exempt=False,
                    canonical_findability=relic_findability,
                    secondary_findability=relic_findability_secondary or (),
                    hunt_number=repo.next_hunt_number(),
                )
            except FindabilityRefused as e:
                # Its own message is written to be leak-free (see
                # relic_findability). Interpolated deliberately, and ONLY for
                # this type.
                return f"⛔ launch relic RECUSADO: {e}"
            except Exception as e:  # noqa: BLE001 — a refusal is the safe outcome
                # Belt and braces for every OTHER failure: an arbitrary
                # exception raised anywhere inside staging may carry the relic
                # NAME in its message, and this line renders straight into the
                # operator's Telegram — a path the identity-leak backstop never
                # runs on (audit 2026-08-26, P0-3). So the type is reported and
                # the text is not. The full exception still reaches the logs.
                import logging

                logging.getLogger(__name__).exception("relic launch staging failed")
                return (
                    f"⛔ launch relic RECUSADO ({type(e).__name__}). "
                    "Detalhe nos logs — a mensagem é omitida aqui porque pode "
                    "conter o nome do relic."
                )
            # The confirmation is bound to the RELIC ID, not a handle — the
            # operator must never be shown the name, so there is nothing else
            # to bind to. `_identity` stays in memory and goes no further.
            launch_confirm.stage(prize_fmml, summary.relic_id)
            return prompt

        try:
            pool = repo.dressed_personas()
        except Exception as e:  # noqa: BLE001
            return f"⚠️ could not read the dressed pool ({e!r}) — try again."
        if not pool:
            return "⛔ dressed pool VAZIA — corre /dress primeiro. Nada lançado."
        # Findability gate (2026-08-20): dressed-while-warmup personas sit in
        # the pool indexing, but only findability-ready ones can carry a hunt.
        eligible, waiting = split_dressed_by_findability(
            pool, min_days=s.min_warmup_days
        )
        if not eligible:
            return (
                "⛔ pool vestida mas NENHUMA persona findability-ready — nada "
                "lançado. A aquecer: "
                + "; ".join(_waiting_line(r, at) for r, at in waiting)
            )
        nxt = eligible[0]
        name = str(nxt.get("applied_display_name") or "?")
        age_line = ""
        dressed_at = str(nxt.get("dressed_at") or "")
        if dressed_at:
            try:
                from datetime import datetime, timezone

                dt = datetime.fromisoformat(dressed_at.replace("Z", "+00:00"))
                age_line = f", vestida há {(datetime.now(timezone.utc) - dt).days}d"
            except ValueError:
                pass
        try:
            number = repo.next_hunt_number()
        except Exception:  # noqa: BLE001
            number = "?"
        # Eligibility floor no prompt (Opus, Fase 3): a última vista de olhos
        # cobre a config TODA — o susto do floor do Hunt #4 não se repete.
        floor = int(getattr(s, "holding_floor_fmml", 0) or 0)
        if floor:
            floor_line = (
                f"floor: {floor:,} $FIND no claim para 100% — non-holders "
                f"ganham {s.non_holder_prize_pct}%."
            )
        elif s.holding_floor_usd > 0:
            floor_line = (
                f"floor: fallback USD (${s.holding_floor_usd:g}, convertido no "
                f"launch) — non-holders ganham {s.non_holder_prize_pct}%."
            )
        else:
            floor_line = "🚨 floor ZERO — qualquer wallet ganha 100% do pote."
        launch_confirm.stage(prize_fmml, str(nxt.get("handle") or ""))
        skipped_line = (
            "(saltadas, ainda em warmup: "
            + "; ".join(_waiting_line(r, at) for r, at in waiting) + ")\n"
            if waiting else ""
        )
        return (
            f"Hunt #{number}: {prize_fmml:,} $FIND com a persona "
            f"{nxt.get('handle')} ('{name}'{age_line}).\n"
            + skipped_line
            + f"{floor_line}\n"
            "⚠️ O launch é INSTANTÂNEO — Clue 1 sai em segundos, sem take-backs.\n"
            "Confirmar? responde 'sim' ou 'não' (expira em 2 min)."
        )

    def _do_launch(prize_fmml: int) -> str:
        """The confirmed launch — guards + preflight + the hunt thread."""
        with hunt_lock:
            if hunt_flag["active"]:
                return "⛔ a hunt is already LIVE — one at a time. /status for details."
            hunt_flag["active"] = True
        # The flag only guards a double-tap in THIS process. The DB is the real
        # source of truth across restarts and deploy overlaps (Railway starts
        # the new container before the old one dies): a resumed hunt or one
        # launched by the other instance only shows up in the hunts table.
        refusal = active_hunt_guard(repo)
        if refusal:
            hunt_flag["active"] = False
            return refusal
        try:
            problems = preflight_check(
                anthropic=anthropic, anthropic_model=s.anthropic_model, openai=openai, x=x
            )
            # Money checks: RPC alive, gas in the hot wallet, tokens >= prize.
            problems += preflight_money(
                web3=web3, payout=payout_engine,
                hot_address=hot_address, prize_fmml=prize_fmml,
            )
            if not hot_address:
                problems.append("hot wallet key missing/invalid — cannot pay a winner")
        except Exception as e:  # noqa: BLE001
            problems = [f"preflight crashed: {e!r}"]
        if problems:
            hunt_flag["active"] = False
            return (
                "⚠️ Pre-flight FAILED — hunt NOT launched:\n"
                + "\n".join(f"• {p}" for p in problems)
                + "\nCheck the keys/billing (and enable auto-recharge), then try again."
            )
        def _run_hunt():
            # Last line of defence: the loop itself survives transient errors,
            # but if anything DOES escape (bug, unrecoverable failure), the
            # operator must hear about it on Telegram — never a silent death.
            try:
                orchestrator.run_hunt(prize_fmml=prize_fmml)
            except Exception as e:  # noqa: BLE001
                import traceback

                traceback.print_exc()
                notifier.notify(
                    f"🚨 HUNT DIED with an unhandled error: {e!r}. "
                    "The persona may still be dressed and players may be mid-game — "
                    "intervene NOW (check the persona profile and pending DMs)."
                )
            finally:
                hunt_flag["active"] = False

        threading.Thread(target=_run_hunt, daemon=True).start()
        # Pre-dressed launch (Fase 2): no prep window — the persona comes
        # dressed+indexed from the pool; R3 verifies; Clue 1 fires in seconds.
        return (
            f"confirmado — hunt launching with a {prize_fmml:,} $FIND prize 🏴 "
            "— persona do pool (a mais antiga), verificação R3, Clue 1 em "
            "segundos. Se a R3 falhar, recebes o alerta e nada é publicado."
        )

    def _on_text(text: str) -> str | None:
        """Plain-text admin messages: only the launch confirmation reads them.
        Free text with nothing staged is ignored (None = no reply)."""
        res = launch_confirm.resolve(text)
        if res.outcome == "confirm":
            # Relic mode binds the prompt to a RELIC ID, not a handle. The check
            # is the same one — never confirm one target and launch another —
            # but against the relic pool; reading the persona pool here would
            # always disagree (it is empty in this mode) and refuse every launch.
            if s.relic_launch:
                if relic_pool is None:
                    return "⛔ relic_pool indisponível — corre /launch de novo."
                try:
                    relic, _identity = relic_pool.peek_launchable()
                except Exception as e:  # noqa: BLE001
                    return f"⚠️ pool de relics ilegível ({e!r}) — corre /launch de novo."
                if str(relic.id) != str(res.expected_handle):
                    return (
                        f"⛔ o relic mais antigo mudou desde o prompt "
                        f"(era {res.expected_handle}) — corre /launch de novo."
                    )
                return _do_launch(res.prize_fmml)
            # The persona shown in the prompt must still be the pool's oldest —
            # never confirm one persona and launch another.
            try:
                pool = repo.dressed_personas()
            except Exception as e:  # noqa: BLE001
                return f"⚠️ pool unreadable at confirm ({e!r}) — corre /launch de novo."
            eligible, _w = split_dressed_by_findability(
                pool, min_days=s.min_warmup_days
            )
            current = str(eligible[0].get("handle") or "") if eligible else ""
            if current != res.expected_handle:
                return (
                    f"⛔ a pool mudou desde o prompt (era {res.expected_handle}, "
                    f"agora {current or 'vazia'}) — corre /launch de novo."
                )
            return _do_launch(res.prize_fmml)
        if res.outcome == "cancel":
            return "launch cancelado. Nada foi publicado."
        if res.outcome == "expired":
            return "a confirmação expirou (2 min) — corre /launch de novo."
        if res.outcome == "noise":
            return "responde 'sim' para lançar ou 'não' para cancelar."
        return None  # outcome 'none': free text, stay silent

    # ------------------------------------------------------------------
    # /dress <ref> [handle hint...] — pre-dress a persona (design 2026-08-12).
    # Runs in the background (LLM identity + 2 image generations + spaced
    # anchor posts take minutes); the pipeline reports on Telegram when done.
    # Refused while a hunt is active: dressing shares the X app's rate
    # budget, and the doctrine is to never touch the agent mid-hunt.
    # ------------------------------------------------------------------
    dress_pipeline = DressPipeline(
        repo=repo,
        token_resolver=env_token_resolver,
        generator=PersonaGenerator(anthropic, s.anthropic_model),
        avatar_generator=AvatarGenerator(
            openai, model=s.openai_image_model, size=s.openai_image_size
        ),
        dresser=PersonaDresser(x),
        post_engine=PersonaPostEngine(anthropic, s.anthropic_model),
        notifier=notifier,
        avatar_writer=write_temp_png,
        register=s.persona_register,
    )
    dress_flag = {"active": False}

    def _dress(arg: str) -> str:
        parts = arg.split(maxsplit=1)
        if not parts:
            return (
                "usage: /dress <ref|handle> [handle hint]\n"
                "e.g. /dress 07 charging = what a phone does plugged in; "
                "capas = capes in PT"
            )
        ref = parts[0]
        hint = parts[1].strip() if len(parts) > 1 else None
        refusal = active_hunt_guard(repo)
        if refusal:
            return f"⛔ not dressing during an active hunt. {refusal}"
        with hunt_lock:
            if dress_flag["active"]:
                return "⛔ a /dress is already running — wait for its report."
            dress_flag["active"] = True

        def _run_dress():
            try:
                dress_pipeline.dress(ref, handle_hint=hint)
            except Exception as e:  # noqa: BLE001
                notifier.notify(f"🚨 /dress {ref} FAILED: {e}")
            finally:
                dress_flag["active"] = False

        threading.Thread(target=_run_dress, daemon=True).start()
        return (
            f"a vestir a persona {ref} em background — identidade, código, "
            "avatar, banner, locator + anchor posts. Relatório aqui quando "
            "terminar (sem o código: continuas cego 🐸)."
        )

    def _status(arg: str = "") -> str:
        """State + the config the agent ACTUALLY loaded.

        Not what's typed in the secrets dashboard — what reached this process.
        That's a step further down the chain, so it also catches secrets that
        never synced, a deploy that didn't restart, or a stale cache. Read it
        before every /launch.
        """
        # Headline from the DB, not this process's memory: after a restart the
        # resumed hunt is invisible to hunt_flag (post-mortem bonus finding A).
        # Pause state comes from the hunt row itself (hunts.paused).
        state = hunt_status_line(repo, local_active=hunt_flag["active"])

        lines = [state, ""]

        floor = int(getattr(s, "holding_floor_fmml", 0) or 0)
        if floor:
            lines.append(f"floor: {floor:,} $FIND | hold: {s.holding_hours}h")
        else:
            # No fixed floor -> falls back to a USD conversion at trigger time.
            # If holding_floor_usd is also 0, the floor becomes ZERO and anyone
            # can claim — silently. Shout about it.
            lines.append(
                f"floor: ⚠️ USD fallback (${s.holding_floor_usd:g}) | hold: {s.holding_hours}h"
                + ("  🚨 FLOOR IS ZERO — anyone can claim!" if not s.holding_floor_usd else "")
                + (
                    "  🚨 price NOT SET — /launch will REFUSE (set HOLDING_FLOOR_FMML or FMML_USD_PRICE)"
                    if s.holding_floor_usd and not s.fmml_usd_price else ""
                )
            )

        worst = worst_case_hunt_hours(s.clue_max_gap_s)
        ok = holding_window_covers_hunt(s.holding_hours, s.clue_max_gap_s)
        lines.append(
            f"clues: {s.clue_min_gap_s // 60}-{s.clue_max_gap_s // 60}min "
            f"→ worst case {worst:.1f}h "
            + (
                f"✅ (< {s.holding_hours}h)"
                if ok
                else f"❌ EXCEEDS the {s.holding_hours}h window — a mid-hunt buyer could win"
            )
        )
        lines.append(
            f"prize min: {fmt_tokens(s.min_prize_fmml)} $FIND "
            f"(/launch {fmt_tokens(s.min_prize_fmml)}) | non-holder share: "
            f"{s.non_holder_prize_pct}%"
        )

        # Pre-dressed pool (Fase 2): what /launch will actually pick from.
        try:
            pool = repo.dressed_personas()
            if pool:
                from datetime import datetime, timezone

                eligible, waiting = split_dressed_by_findability(
                    pool, min_days=s.min_warmup_days
                )
                oldest = eligible[0] if eligible else pool[0]
                d = str(oldest.get("dressed_at") or "")
                age = ""
                if d:
                    dt = datetime.fromisoformat(d.replace("Z", "+00:00"))
                    age = f", a mais antiga há {(datetime.now(timezone.utc) - dt).days}d"
                nxt_line = (
                    f"next: {oldest.get('handle')}"
                    if eligible else "next: NENHUMA findability-ready"
                )
                wait_line = (
                    " | a aquecer: "
                    + "; ".join(_waiting_line(r, at) for r, at in waiting)
                    if waiting else ""
                )
                lines.append(
                    f"dressed pool: {len(pool)} persona(s){age} — {nxt_line}{wait_line}"
                )
            else:
                lines.append("dressed pool: VAZIA — /dress antes de /launch")
        except Exception:  # noqa: BLE001 — cosmetic, never breaks /status
            pass

        if s.fmml_usd_price:
            one_b = 1_000_000_000 * s.fmml_usd_price
            lines.append(f"price (info): {s.fmml_usd_price:g} → 1B ≈ ${one_b:.0f}")
        else:
            lines.append("price: not set (ok — /launch takes $FIND amounts)")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Daily oracle post (non-game 'filler'): drafts are generated on demand
    # (/tease) or on a daily schedule, queued for approval, and ONLY publish
    # via /approve. Game posts never pass through here.
    # ------------------------------------------------------------------
    filler = FillerEngine(anthropic, s.anthropic_model)
    approval_queue = ApprovalQueue(repo=repo, publisher=XPublisher(x))

    def _draft_and_queue(topic: str | None) -> str:
        options = filler.generate_options(topic=topic)
        lines = ["🐸 oracle drafts" + (f" — topic: {topic}" if topic else " (daily)") + ":"]
        for opt in options:
            approval_id = approval_queue.submit_for_approval(kind="filler", draft_text=opt)
            lines.append(f"\n#{approval_id}:\n{opt}")
        lines.append(
            "\n✅ /approve <id> — publica · ✏️ /approve <id> <texto> — publica editado "
            "· ❌ /reject <id>"
        )
        return "\n".join(lines)

    def _tease(arg: str = "") -> str:
        try:
            return _draft_and_queue(arg.strip() or None)
        except Exception as e:  # noqa: BLE001
            return f"draft generation failed: {e!r}"

    def _approve(arg: str = "") -> str:
        parts = arg.split(maxsplit=1)
        if not parts or not parts[0].isdigit():
            return "usage: /approve <id> [edited text]"
        try:
            return approval_queue.decide(
                int(parts[0]), "approve",
                edited_text=parts[1].strip() if len(parts) > 1 else None,
            )
        except Exception as e:  # noqa: BLE001
            return f"approve failed: {e!r}"

    def _reject(arg: str = "") -> str:
        if not arg.strip().isdigit():
            return "usage: /reject <id>"
        try:
            return approval_queue.decide(int(arg.strip()), "reject")
        except Exception as e:  # noqa: BLE001
            return f"reject failed: {e!r}"

    # Fase 3: the prep window is dead in production (pre-dressed launches skip
    # it), so its two operator commands are retired to honest stubs. The
    # window's code stays in the orchestrator for simulation/live-test only.
    _PREP_RETIRED = (
        "comando reformado (Fase 3): já não há prep window — o /launch é "
        "instantâneo com confirmação sim/não. Antes da Clue 1 não há nada para "
        "abortar/adiar; depois dela o hunt está público."
    )

    def _abort_prep(arg: str = "") -> str:
        return _PREP_RETIRED

    def _delay_golive(arg: str = "") -> str:
        return _PREP_RETIRED

    def _silence(arg: str = "") -> str:
        try:
            n = control.pause()
        except Exception as e:  # noqa: BLE001 — kill switch MUST be honest
            return f"⚠️ pause NOT applied (DB write failed: {e!r}) — try again."
        if not n:
            return "no active hunt in the DB — nothing to pause."
        return (
            "⏸ paused (persisted on the hunt row — survives restarts). "
            "Hunt loop idling: no clues, no DM processing, no payouts. "
            "DMs keep accumulating on X (arrival order preserved). /resume to continue."
        )

    def _resume(arg: str = "") -> str:
        try:
            n = control.resume()
        except Exception as e:  # noqa: BLE001
            return f"⚠️ resume NOT applied (DB write failed: {e!r}) — try again."
        return "▶️ resumed." if n else "no active hunt in the DB — nothing to resume."

    # Daily scheduler: at filler_hour_utc, generate drafts and push them to the
    # admin's Telegram. Best-effort — a failed day never breaks anything.
    def _daily_filler_loop():
        from datetime import datetime, timedelta, timezone
        import time as _time

        while True:
            now = datetime.now(timezone.utc)
            target = now.replace(hour=s.filler_hour_utc, minute=0, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            _time.sleep((target - now).total_seconds())
            try:
                notifier.notify(_draft_and_queue(None))
            except Exception as e:  # noqa: BLE001
                notifier.notify(f"daily oracle drafts failed (will retry tomorrow): {e!r}")

    if s.filler_daily_enabled and s.telegram_bot_token and s.telegram_admin_chat_id:
        threading.Thread(target=_daily_filler_loop, daemon=True).start()

    # Watchdog: the Telegram "scream" (post-mortem P0 pack). Checks the
    # heartbeat every minute; only alerts while a hunt is live and the loop
    # stopped completing cycles. Best-effort — must never die.
    def _watchdog_loop():
        import time as _time

        while True:
            _time.sleep(60)
            try:
                alert = heartbeat.check()
                if alert:
                    notifier.notify(alert)
            except Exception as e:  # noqa: BLE001
                print(f"[watchdog] check failed (non-fatal): {e!r}")

    threading.Thread(target=_watchdog_loop, daemon=True).start()

    def _relic_new(arg: str = "") -> str:
        """Create N relic identities (default 1), stored ENCRYPTED.

        Mints nothing and spends no gas. NEVER returns a name: blind mode has to
        hold in this message too, or the whole architecture is theatre."""
        if relic_pool is None or relic_generator is None:
            return "⛔ relic_pool_key not configured — no pool without it."
        try:
            n = max(1, min(10, int(arg or "1")))
        except ValueError:
            return "usage: /relic_new [1-10]"

        from .persona.relic_mint import create_relic

        created: list[str] = []
        failures: list[str] = []
        for _ in range(n):
            try:
                created.append(create_relic(pool=relic_pool, generator=relic_generator))
            except Exception as e:  # noqa: BLE001 — one bad draw must not stop the rest
                failures.append(repr(e)[:120])

        lines = [f"✅ created {len(created)}/{n} (names NOT shown — blind mode)"]
        lines += [f"  · {rid}" for rid in created]
        if failures:
            lines.append(f"⚠️ {len(failures)} failed:")
            lines += [f"  · {f}" for f in failures]
        try:
            lines.append(
                f"pool: {relic_pool.relic_count()} relics, "
                f"{len(relic_pool.spent_words())} words spent"
            )
        except Exception:  # noqa: BLE001 — the tally is cosmetic
            pass
        return "\n".join(lines)

    def _relic_mint(arg: str = "") -> str:
        """Mint the oldest un-minted relic in the pool (or a specific id).

        One relic per call, deliberately: a mint spends real gas and burns a
        wallet forever, so a mistyped argument must never start a batch. It also
        keeps the minting cadence IRREGULAR by hand — a regular pattern is the
        one thing revealed relics teach an observer about future ones."""
        if relic_pool is None:
            return "⛔ relic_pool_key not configured."
        missing = [
            label for label, ok in (
                ("RELIC_WALLET_REFS", bool(s.relic_wallet_ref_list)),
                ("PINATA_JWT", bool(s.pinata_jwt)),
                ("contracts/RelicNFT.json", relic_minter is not None),
            ) if not ok
        ]
        if missing:
            return "⛔ minting not configured — missing: " + ", ".join(missing)

        from .persona.relic_mint import mint_relic

        relic_id = arg.strip()
        if not relic_id:
            unminted = relic_pool.unminted_relics()
            if not unminted:
                return "⛔ no un-minted relic in the pool — /relic_new first."
            relic_id = unminted[0].id

        try:
            free = len(relic_wallets.free_refs())
        except Exception:  # noqa: BLE001
            free = -1
        try:
            result = mint_relic(
                relic_id=relic_id, pool=relic_pool, wallets=relic_wallets,
                image_gen=relic_artwork, minter=relic_minter,
            )
        except Exception as e:  # noqa: BLE001
            return f"⛔ mint FAILED for {relic_id}: {e!r}"

        return (
            f"✅ minted (name NOT shown — blind mode)\n"
            f"  relic:    {relic_id}\n"
            f"  contract: {result.contract}\n"
            f"  token:    {result.token_id}\n"
            f"  tx:       {result.tx_hash}\n"
            f"  wallets left: {free - 1 if free >= 0 else '?'}"
        )

    actions = {
        "launch": _launch,
        "relic_new": _relic_new,
        "relic_mint": _relic_mint,
        "dress": _dress,
        "status": _status,
        "silence": _silence,
        "resume": _resume,
        "abort_prep": _abort_prep,
        "delay_golive": _delay_golive,
        "tease": _tease,
        "approve": _approve,
        "reject": _reject,
    }
    telegram = TelegramAdmin(
        bot_token=s.telegram_bot_token, admin_chat_id=s.telegram_admin_chat_id,
        approval=approval_queue, actions=actions, on_text=_on_text,
    )
    return Agent(orchestrator=orchestrator, telegram=telegram, repo=repo)


def main() -> None:
    s = get_settings()
    agent = build_agent(s)
    # Token prizes (2026-07-31): a price is no longer required to launch.
    token_ready = bool(s.fmml_token_address)
    print(f"[finding-memeland] agent built (env={s.fmml_env}). hunt-ready: {token_ready}")

    # Crash recovery: if the previous process died mid-hunt, pick it back up
    # (LIVE hunts continue; money-adjacent states alert for manual settling).
    import threading

    threading.Thread(target=agent.orchestrator.resume_hunts, daemon=True).start()

    if s.telegram_bot_token and s.telegram_admin_chat_id:
        print("  starting Telegram admin loop — send /status or /launch from the admin chat.")
        agent.telegram.run()  # blocks, polling for admin commands
    else:
        print("  TELEGRAM_BOT_TOKEN / TELEGRAM_ADMIN_CHAT_ID not set — staying idle.")


if __name__ == "__main__":
    main()
