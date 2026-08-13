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
    from .persona.source import DBPersonaSource
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
    # Watchdog sensor: the hunt loop beats every cycle; a supervisor thread
    # below screams on Telegram if beats stop while a hunt is live (P0 pack).
    heartbeat = PollHeartbeat(stall_after_s=s.watchdog_stall_s)
    web3 = Web3(Web3.HTTPProvider(s.base_rpc_url))
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
        persona_source=DBPersonaSource(repo, env_token_resolver),
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
        # P2: prepare/go-live split — /launch = T-prep_window_h; Clue 1 at T0.
        persona_post_engine=PersonaPostEngine(anthropic, s.anthropic_model),
        prep_window_h=s.prep_window_h or None,
        prep_posts_n=s.prep_posts_n,
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
            f"hunt launching with a {prize_fmml:,} $FIND prize 🏴 — persona do "
            "pool (a mais antiga), verificação R3, Clue 1 em segundos. "
            "Se a R3 falhar, recebes o alerta e nada é publicado."
        )

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

                oldest = pool[0]
                d = str(oldest.get("dressed_at") or "")
                age = ""
                if d:
                    dt = datetime.fromisoformat(d.replace("Z", "+00:00"))
                    age = f", a mais antiga há {(datetime.now(timezone.utc) - dt).days}d"
                lines.append(
                    f"dressed pool: {len(pool)} persona(s){age} — next: "
                    f"{oldest.get('handle')}"
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

    def _prepped_hunt():
        """The hunt currently in its prep window, from the DB (source of truth)."""
        rows = repo.active_hunts()
        for r in rows:
            if r.get("state") == "prepped":
                return r
        return None

    def _abort_prep(arg: str = "") -> str:
        try:
            row = _prepped_hunt()
            if row is None:
                return "no hunt in a prep window — nothing to abort."
            repo.update_hunt(row["id"], abort_prep=True)
        except Exception as e:  # noqa: BLE001
            return f"⚠️ abort NOT applied (DB write failed: {e!r}) — try again."
        return (
            "🛑 prep ABORT requested (persisted). The prep loop will undress and "
            "void the persona within ~1 min. Clue 1 will NOT fire."
        )

    def _delay_golive(arg: str = "") -> str:
        try:
            hours = float(arg) if arg.strip() else 24.0
        except ValueError:
            return f"'{arg}' isn't a number of hours. usage: /delay_golive <h>"
        if hours <= 0:
            return "delay must be positive. usage: /delay_golive <h>"
        try:
            from datetime import datetime, timedelta, timezone

            row = _prepped_hunt()
            if row is None:
                return "no hunt in a prep window — nothing to delay."
            base = str(row.get("golive_due_at") or "")
            due = (
                datetime.fromisoformat(base.replace("Z", "+00:00"))
                if base else datetime.now(timezone.utc)
            )
            new_due = due + timedelta(hours=hours)
            repo.update_hunt(row["id"], golive_due_at=new_due)
        except Exception as e:  # noqa: BLE001
            return f"⚠️ delay NOT applied (DB write failed: {e!r}) — try again."
        return f"⏳ go-live pushed +{hours:g}h → {new_due:%Y-%m-%d %H:%M} UTC (persisted)."

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

    actions = {
        "launch": _launch,
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
        approval=approval_queue, actions=actions,
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
