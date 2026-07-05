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
    from .content.clue_engine import ClueEngine
    from .db.client import Repo, make_client
    from .dm.listener import XDMSource
    from .dm.validator import DMValidator
    from .orchestrator.state_machine import Orchestrator
    from .persona.avatar import AvatarGenerator
    from .persona.dresser import PersonaDresser
    from .persona.generator import PersonaGenerator
    from .persona.source import DBPersonaSource
    from .runtime import (
        ManualPriceFeed,
        StdoutNotifier,
        SystemClock,
        env_token_resolver,
        write_temp_png,
    )
    from .preflight import preflight_check
    from .social.publisher import XPublisher
    from .social.x_client import XClient
    from .telegram.approval_queue import ApprovalQueue, TelegramAdmin

    anthropic = Anthropic(api_key=s.anthropic_api_key)
    openai = OpenAI(api_key=s.openai_api_key, max_retries=4, timeout=120.0)
    repo = Repo(make_client(s.supabase_url, s.supabase_service_role_key))
    web3 = Web3(Web3.HTTPProvider(s.base_rpc_url))
    x = XClient(
        api_key=s.x_api_key, api_secret=s.x_api_secret, bearer_token=s.x_bearer_token,
        main_access_token=s.x_main_access_token, main_access_secret=s.x_main_access_secret,
    )

    holdings = Holdings(web3=web3, token_address=s.fmml_token_address, repo=repo)
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
        payout=PayoutEngine(
            web3=web3, token_address=s.fmml_token_address,
            hot_wallet_key=s.hot_wallet_private_key, per_hunt_cap=int(s.payout_cap_fmml or 0),
        ),
        price_feed=ManualPriceFeed(s.fmml_usd_price),
        notifier=StdoutNotifier(),
        register=s.persona_register,
        holding_floor_usd=s.holding_floor_usd,
        holding_hours=s.holding_hours,
        avatar_writer=write_temp_png,
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

    def _launch(arg: str) -> str:
        if not arg:
            return f"usage: /launch <prize in $>, e.g. /launch 250{_suggestion()}"
        try:
            prize_usd = float(arg)
        except ValueError:
            return f"'{arg}' isn't a number. usage: /launch <prize in $>"
        if prize_usd < s.min_prize_usd:
            return f"minimum prize is ${s.min_prize_usd:.0f} — nobody plays for less."
        problems = preflight_check(
            anthropic=anthropic, anthropic_model=s.anthropic_model, openai=openai, x=x
        )
        if problems:
            return (
                "⚠️ Pre-flight FAILED — hunt NOT launched:\n"
                + "\n".join(f"• {p}" for p in problems)
                + "\nCheck the keys/billing (and enable auto-recharge), then try again."
            )
        threading.Thread(target=lambda: orchestrator.run_hunt(prize_usd=prize_usd), daemon=True).start()
        return f"hunt launching with a ${prize_usd:.0f} prize 🏴"

    actions = {
        "launch": _launch,
        "status": lambda arg="": "idle",
        "silence": lambda arg="": "pause requested (supervisor wiring pending)",
        "resume": lambda arg="": "resume requested",
    }
    telegram = TelegramAdmin(
        bot_token=s.telegram_bot_token, admin_chat_id=s.telegram_admin_chat_id,
        approval=ApprovalQueue(repo=repo, publisher=XPublisher(x)), actions=actions,
    )
    return Agent(orchestrator=orchestrator, telegram=telegram, repo=repo)


def main() -> None:
    s = get_settings()
    agent = build_agent(s)
    token_ready = bool(s.fmml_token_address and s.fmml_usd_price > 0)
    print(f"[finding-memeland] agent built (env={s.fmml_env}). hunt-ready: {token_ready}")

    if s.telegram_bot_token and s.telegram_admin_chat_id:
        print("  starting Telegram admin loop — send /status or /launch from the admin chat.")
        agent.telegram.run()  # blocks, polling for admin commands
    else:
        print("  TELEGRAM_BOT_TOKEN / TELEGRAM_ADMIN_CHAT_ID not set — staying idle.")


if __name__ == "__main__":
    main()
