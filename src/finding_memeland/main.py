"""Entrypoint + composition root.

    doppler run -- python -m finding_memeland.main

`build_agent` is the single place that constructs the heavy clients (Anthropic,
OpenAI, web3, Supabase, X) and wires them, via the runtime adapters, into the
Orchestrator. Everything else in the codebase depends only on the ports, so this
is the one import-heavy module.

The agent boots idle. Hunts fire on the admin's Telegram /launch (the bot loop —
TelegramAdmin.build_application — is the final live wiring step). run_hunt()
fails fast via settings.assert_ready_for_hunt() if token/wallet/price aren't set.
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
    from .content.filler import FillerEngine
    from .db.client import Repo, make_client
    from .dm.listener import XDMSource
    from .dm.validator import DMValidator
    from .orchestrator.state_machine import Orchestrator
    from .persona.avatar import AvatarGenerator
    from .persona.dresser import PersonaDresser
    from .persona.generator import PersonaGenerator
    from .persona.source import DBPersonaSource
    from .runtime import (
        HuntControl,
        ManualPriceFeed,
        StdoutNotifier,
        SystemClock,
        TelegramNotifier,
        env_token_resolver,
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
    control = HuntControl()  # /silence // /resume kill switch

    anthropic = Anthropic(api_key=s.anthropic_api_key)
    openai = OpenAI(api_key=s.openai_api_key, max_retries=4, timeout=120.0)
    repo = Repo(make_client(s.supabase_url, s.supabase_service_role_key))
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
        # Real hunts NEVER undress the persona: single-use accounts, and the
        # dressed profile stays up as the hunt's public artifact.
        undress_on_retire=False,
    )

    # Admin/approval surface. /launch <prize_usd> fires a hunt in the BACKGROUND
    # (it can run for hours) so the bot stays responsive. The prize is set per hunt
    # by the operator; the agent converts $ -> $FIND at the current price and posts
    # the token amount. The dynamic rule (economics.suggested_prize) is a guide.
    import threading

    from .economics import fdv_from_price, suggested_prize

    def _suggestion() -> str:
        try:
            fdv = fdv_from_price(s.fmml_usd_price, s.total_supply) if s.fmml_usd_price else 0
        except Exception:  # noqa: BLE001
            fdv = 0
        return f" (suggested at current FDV: ${suggested_prize(fdv):.0f})" if fdv else ""

    # One hunt at a time: a double-tapped /launch must never start two hunts
    # (two personas, two prize payouts, one shared DM stream).
    hunt_flag = {"active": False}
    hunt_lock = threading.Lock()

    def _launch(arg: str) -> str:
        if not arg:
            return f"usage: /launch <prize in $>, e.g. /launch 250{_suggestion()}"
        try:
            prize_usd = float(arg)
        except ValueError:
            return f"'{arg}' isn't a number. usage: /launch <prize in $>"
        if prize_usd < s.min_prize_usd:
            return f"minimum prize is ${s.min_prize_usd:.0f} — nobody plays for less."
        with hunt_lock:
            if hunt_flag["active"]:
                return "⛔ a hunt is already LIVE — one at a time. /status for details."
            hunt_flag["active"] = True
        try:
            problems = preflight_check(
                anthropic=anthropic, anthropic_model=s.anthropic_model, openai=openai, x=x
            )
            # Money checks: RPC alive, gas in the hot wallet, tokens >= prize.
            prize_fmml = price_feed.usd_to_fmml(prize_usd)
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
                orchestrator.run_hunt(prize_usd=prize_usd)
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
        return f"hunt launching with a ${prize_usd:.0f} prize 🏴"

    def _status(arg: str = "") -> str:
        """State + the config the agent ACTUALLY loaded.

        Not what's typed in the secrets dashboard — what reached this process.
        That's a step further down the chain, so it also catches secrets that
        never synced, a deploy that didn't restart, or a stale cache. Read it
        before every /launch.
        """
        state = "hunt: LIVE" if hunt_flag["active"] else "hunt: none (idle)"
        if control.paused():
            state += " | ⏸ PAUSED (/resume to continue)"

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
        lines.append(f"prize min: ${s.min_prize_usd:.0f}")

        if s.fmml_usd_price:
            one_b = 1_000_000_000 * s.fmml_usd_price
            lines.append(f"price: {s.fmml_usd_price:g} → 1B = ${one_b:.0f} (/launch {one_b:.0f})")
            if one_b < s.min_prize_usd:
                lines.append(f"  ⚠️ 1B is BELOW the ${s.min_prize_usd:.0f} minimum — /launch would refuse")
        else:
            lines.append("price: ⚠️ NOT SET — /launch cannot convert $ → $FIND")

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

    def _silence(arg: str = "") -> str:
        control.pause()
        return (
            "⏸ paused. Hunt loop idling: no clues, no DM processing, no payouts. "
            "DMs keep accumulating on X (arrival order preserved). /resume to continue."
        )

    def _resume(arg: str = "") -> str:
        control.resume()
        return "▶️ resumed."

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

    actions = {
        "launch": _launch,
        "status": _status,
        "silence": _silence,
        "resume": _resume,
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
    token_ready = bool(s.fmml_token_address and s.fmml_usd_price > 0)
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
