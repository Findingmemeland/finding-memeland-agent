"""Live test harness — run a REAL hunt on X with the on-chain parts stubbed.

Validates the FULL mechanic end-to-end against the real X API — persona
dressing, Clue 1 + integrity hash, progressive clues, findability, the DM claim
flow, the reshare gate, the bot screen, the winner reveal and persona retire —
AND the Telegram /launch trigger, WITHOUT a token, a prize, or a wallet.

REAL:  X (profile write, posts, DM read/reply, reshare check, user lookup),
       Anthropic (persona + clues), OpenAI (avatar/banner), Telegram.
STUB:  holdings check (always passes), payout (no transfer), price feed
       (cosmetic), and the token/wallet readiness gate.

Run:   PYTHONPATH=src python -m finding_memeland.live_test
Then send `/launch 200` from the admin Telegram chat. Clues drop every
FMML_TEST_CLUE_INTERVAL_S seconds (default 120); the reveal window is
FMML_TEST_CLEANUP_S (default 120). The DB is in-memory (no Supabase writes).

⚠️ NEVER point this at a real prize hunt — it pays nobody and skips holdings.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .config import Settings, get_settings
from .orchestrator.ports import PayoutReceipt, ReadyPersona


@dataclass
class Agent:
    orchestrator: object
    telegram: object


class _TestSettings:
    """Forwards to the real Settings but skips the token/wallet readiness gate."""

    def __init__(self, real):
        self._real = real

    def __getattr__(self, name):
        return getattr(self._real, name)

    def assert_ready_for_hunt(self) -> None:
        return None


class _AlwaysHeldHoldings:
    """Holdings stub: every wallet 'holds' — there is no token in the test."""

    def has_continuous_holding(self, **_kwargs) -> bool:
        return True


class _TestPayout:
    """Announces a winner but performs NO on-chain transfer."""

    def send_prize(self, *, hunt_id, to_wallet, amount_fmml) -> PayoutReceipt:
        return PayoutReceipt(tx_hash="TEST-no-onchain-payout", amount_fmml=amount_fmml)


class _TestPriceFeed:
    """Cosmetic $ -> token conversion (no real market price)."""

    def usd_to_fmml(self, usd: float) -> int:
        return int(usd * 1000)


class _EnvTestPersonaSource:
    """One test persona, tokens + identity from env. No DB, no readiness gate."""

    def __init__(self):
        tok = os.environ.get("X_TEST_PERSONA_ACCESS_TOKEN", "")
        sec = os.environ.get("X_TEST_PERSONA_ACCESS_SECRET", "")
        uid = os.environ.get("X_TEST_PERSONA_USER_ID", "")
        handle = os.environ.get("X_TEST_PERSONA_HANDLE", "")
        missing = [
            n
            for n, v in {
                "X_TEST_PERSONA_ACCESS_TOKEN": tok,
                "X_TEST_PERSONA_ACCESS_SECRET": sec,
                "X_TEST_PERSONA_USER_ID": uid,
                "X_TEST_PERSONA_HANDLE": handle,
            }.items()
            if not v
        ]
        if missing:
            raise RuntimeError(
                "live_test: missing env for the test persona: " + ", ".join(missing)
            )
        self._persona = ReadyPersona(
            id="test-persona",
            handle=handle if handle.startswith("@") else "@" + handle,
            x_user_id=uid,
            access_token=tok,
            access_secret=sec,
        )

    def acquire_ready(self) -> ReadyPersona:
        return self._persona

    def mark_retired(self, persona_id: str) -> None:
        print(f"[live_test] persona {persona_id} retired (profile reset to dormant).")


class _RecordingX:
    """Wraps XClient: forwards every call, but records the tweet ids it posts
    (main account + persona) so the live test can DELETE them all when the hunt
    ends. Only `post`/`post_as_persona` are intercepted; everything else
    delegates to the real client unchanged."""

    def __init__(self, x):
        self._x = x
        self._main_ids: list[str] = []
        self._persona_posts: list[tuple[str, str, str]] = []

    def __getattr__(self, name):
        return getattr(self._x, name)

    def post(self, text, *, long_post=False):
        tid = self._x.post(text, long_post=long_post)
        self._main_ids.append(tid)
        return tid

    def post_as_persona(self, access_token, access_secret, text):
        tid = self._x.post_as_persona(access_token, access_secret, text)
        self._persona_posts.append((access_token, access_secret, tid))
        return tid

    @property
    def posted(self) -> list[str]:
        return self._main_ids + [p[2] for p in self._persona_posts]

    def cleanup(self) -> None:
        for tid in self._main_ids:
            try:
                self._x.delete_post(tid)
            except Exception as e:  # noqa: BLE001
                print(f"[live_test] could not delete main post {tid}: {e!r}")
        for tok, sec, tid in self._persona_posts:
            try:
                self._x.delete_as_persona(tok, sec, tid)
            except Exception as e:  # noqa: BLE001
                print(f"[live_test] could not delete persona post {tid}: {e!r}")


def build_test_agent(settings: Settings | None = None) -> Agent:
    s = settings or get_settings()

    # Heavy clients imported here, exactly like the production composition root.
    from anthropic import Anthropic
    from openai import OpenAI

    from .content.clue_engine import ClueEngine
    from .dm.listener import XDMSource
    from .dm.validator import DMValidator
    from .orchestrator.simulation import FakeRepo
    from .orchestrator.state_machine import Orchestrator
    from .persona.avatar import AvatarGenerator
    from .persona.dresser import PersonaDresser
    from .persona.generator import PersonaGenerator
    from .preflight import preflight_check
    from .runtime import HuntControl, StdoutNotifier, SystemClock, write_temp_png
    from .social.publisher import XPublisher
    from .social.x_client import XClient
    from .telegram.approval_queue import ApprovalQueue, TelegramAdmin

    clue_interval_s = int(os.environ.get("FMML_TEST_CLUE_INTERVAL_S", "45"))
    poll_interval_s = int(os.environ.get("FMML_TEST_POLL_INTERVAL_S", "75"))
    cleanup_s = int(os.environ.get("FMML_TEST_CLEANUP_S", "60"))

    anthropic = Anthropic(api_key=s.anthropic_api_key)
    openai = OpenAI(api_key=s.openai_api_key, max_retries=4, timeout=120.0)
    x = _RecordingX(
        XClient(
            api_key=s.x_api_key,
            api_secret=s.x_api_secret,
            bearer_token=s.x_bearer_token,
            main_access_token=s.x_main_access_token,
            main_access_secret=s.x_main_access_secret,
        )
    )
    repo = FakeRepo()  # in-memory: no Supabase writes, no FK constraints during the test
    control = HuntControl()  # /silence // /resume kill switch (same as production)

    # Chain adapters: stubbed by default; REAL (Base Sepolia + test ERC-20) when
    # FMML_TEST_ONCHAIN=1, to validate the holding check + ERC-20 payout live.
    onchain = os.environ.get("FMML_TEST_ONCHAIN", "").lower() in ("1", "true", "yes")
    holding_hours = int(os.environ.get("FMML_TEST_HOLDING_HOURS", "2"))
    if onchain:
        from web3 import Web3

        from .chain.holdings import Holdings
        from .chain.payout import PayoutEngine
        from .runtime import ManualPriceFeed

        web3 = Web3(Web3.HTTPProvider(s.base_rpc_url))
        chain = Holdings(web3=web3, token_address=s.fmml_token_address, repo=repo)
        payout = PayoutEngine(
            web3=web3,
            token_address=s.fmml_token_address,
            hot_wallet_key=s.hot_wallet_private_key,
            per_hunt_cap=int(s.payout_cap_fmml or 0),
        )
        price_feed = ManualPriceFeed(s.fmml_usd_price)
        # Seed continuity samples for the designated winner wallet(s) so a wallet
        # that REALLY holds the test token passes has_continuous_holding.
        min_bal = price_feed.usd_to_fmml(s.holding_floor_usd)
        now = datetime.now(timezone.utc)
        winners = [w.strip() for w in os.environ.get("FMML_TEST_WINNER_WALLETS", "").split(",") if w.strip()]
        for w in winners:
            repo.seed_holding_sample(w, min_bal * 10, now - timedelta(minutes=30))
            repo.seed_holding_sample(w, min_bal * 10, now)
        print(
            f"[live_test] ON-CHAIN mode — RPC={s.base_rpc_url} token={s.fmml_token_address} "
            f"min_balance={min_bal} winners_seeded={winners}"
        )
    else:
        chain = _AlwaysHeldHoldings()
        payout = _TestPayout()
        price_feed = _TestPriceFeed()

    orchestrator = Orchestrator(
        settings=_TestSettings(s),
        clock=SystemClock(),
        repo=repo,
        persona_source=_EnvTestPersonaSource(),
        persona_generator=PersonaGenerator(anthropic, s.anthropic_model),
        avatar_generator=AvatarGenerator(
            openai, model=s.openai_image_model, size=s.openai_image_size
        ),
        dresser=PersonaDresser(x),
        publisher=XPublisher(x),
        clue_engine=ClueEngine(anthropic, s.anthropic_model),
        dm_source=XDMSource(x),
        validator=DMValidator(
            chain=chain,
            x_client=x,
            profile_lookup=x.lookup_user,
            own_handles=["FindingMemeland"],
        ),
        payout=payout,
        price_feed=price_feed,
        notifier=StdoutNotifier(),
        register=s.persona_register,
        holding_floor_usd=s.holding_floor_usd,
        holding_hours=holding_hours,
        avatar_writer=write_temp_png,
        poll_interval_s=poll_interval_s,
        clue_due_fn=lambda now: now + timedelta(seconds=clue_interval_s),
        cleanup_window_s=cleanup_s,
        # Test-only: DO undress (it's the operator's own account). Real hunts
        # never undress — the persona stays as the hunt's public artifact.
        undress_on_retire=True,
        control=control,
    )

    def _launch(arg: str) -> str:
        try:
            prize_usd = float(arg) if arg else 200.0
        except ValueError:
            return f"'{arg}' isn't a number. usage: /launch <prize $> (cosmetic in test)"

        problems = preflight_check(
            anthropic=anthropic, anthropic_model=s.anthropic_model, openai=openai, x=x
        )
        if problems:
            return "⚠️ Pre-flight FAILED — NOT launching:\n" + "\n".join(f"• {p}" for p in problems)

        def _run():
            try:
                orchestrator.run_hunt(prize_usd=prize_usd)
            except Exception as e:  # noqa: BLE001
                import traceback

                print(f"[live_test] hunt error: {e!r}")
                traceback.print_exc()  # full stack so we can see WHICH call 403'd
            finally:
                print(f"[live_test] hunt finished — deleting {len(x.posted)} test posts on X...")
                x.cleanup()
                print(
                    "[live_test] cleanup done. The persona's PROFILE is left dormant "
                    "(name/bio reset, not your original) — rebrand it as you like."
                )

        threading.Thread(target=_run, daemon=True).start()
        return (
            f"🧪 TEST hunt launching (no token, no payout, holdings skipped). "
            f"Clues ~every {clue_interval_s}s; all posts auto-deleted at the end. 🏴"
        )

    def _post(arg: str) -> str:
        if not arg:
            return "usage: /post <texto a publicar na @FindingMemeland>"
        try:
            tid = x._x.post(arg)  # underlying client: posta manual (não entra na limpeza do hunt)
        except Exception as e:  # noqa: BLE001
            return f"post failed: {e!r}"
        return f"posted ✅ — https://x.com/FindingMemeland/status/{tid}"

    def _silence(arg: str = "") -> str:
        control.pause()
        return "⏸ paused (test). No clues, no DM processing. /resume to continue."

    def _resume(arg: str = "") -> str:
        control.resume()
        return "▶️ resumed."

    actions = {
        "launch": _launch,
        "post": _post,
        "status": lambda arg="": (
            "live-test mode (no token / no payout)"
            + (" | ⏸ PAUSED" if control.paused() else "")
        ),
        "silence": _silence,
        "resume": _resume,
    }
    telegram = TelegramAdmin(
        bot_token=s.telegram_bot_token,
        admin_chat_id=s.telegram_admin_chat_id,
        approval=ApprovalQueue(repo=repo, publisher=XPublisher(x)),
        actions=actions,
    )
    return Agent(orchestrator=orchestrator, telegram=telegram)


def main() -> None:
    # Load .env into os.environ so the X_TEST_PERSONA_* / FMML_TEST_* vars
    # (read via os.environ) work locally without Doppler. No-op if absent.
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:  # noqa: BLE001
        pass

    s = get_settings()
    print("[finding-memeland] LIVE TEST MODE — real X, NO token / NO payout / holdings skipped.")
    agent = build_test_agent(s)
    if s.telegram_bot_token and s.telegram_admin_chat_id:
        print("  Telegram admin loop up. Send /launch 200 from the admin chat to start the test hunt.")
        agent.telegram.run()  # blocks, polling for admin commands
    else:
        print("  TELEGRAM_BOT_TOKEN / TELEGRAM_ADMIN_CHAT_ID not set — cannot run the test.")


if __name__ == "__main__":
    main()
