"""O /status não pode fazer esperar ninguém — o /silence menos que todos.

10/10. O /status passou a medir a pesquisa do mercado nas duas formas, e eu
disse ao Pedro o custo: até 100 s com tudo a esgotar o tempo limite, e,
enquanto corre, os outros comandos do Telegram esperam. A resposta dele: "o
/status não pode bloquear os outros comandos — sobretudo o /silence, que é o
travão de emergência e nunca pode esperar. Põe as medições em paralelo (pior
caso ~50 s) e garante, com teste, que o /silence responde de imediato mesmo
com um /status a meio."

Lido no código nesse dia: o handler do Telegram chamava a acção do comando EM
LINHA, dentro do ciclo de eventos. Enquanto um comando corria, a actualização
seguinte nem era lida. E o /status já perguntava à rede antes disto — a API
do X pela âncora, com uma hunt activa.

O que isto fixa:

  · o /status corre numa thread à parte e a resposta chega quando acaba; o
    handler devolve logo, por isso o comando seguinte é lido de imediato;
  · o /silence (e todos os outros) correm em linha e respondem antes de o
    handler devolver, como sempre;
  · um segundo /status com um a correr não começa outro: diz que espere —
    até 120 s; depois disso o primeiro conta como preso;
  · um /status que rebenta diz que falhou, só com o tipo;
  · só comandos que LÊEM podem sair do ciclo, e o travão nunca.

As medições em paralelo estão em test_search_status.py.
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest
from test_search_status import CHAINS, Search, wiring

from finding_memeland.telegram.approval_queue import (
    OFF_LOOP_COMMANDS,
    OFF_LOOP_STALE_S,
    TELEGRAM_COMMANDS,
    TelegramAdmin,
    route_command,
    split_command,
)

ADMIN = "4242"
WAIT = "⏳ já há um /status a correr — a resposta chega quando terminar"
PAUSED = "⏸ paused"
SECRET = "https://api.example/search?key=Quiet+Lantern+Above"


class Held:
    """Um /status PRESO até o teste o soltar — a pesquisa que não responde."""

    def __init__(self, answer: str = "estado"):
        self.answer = answer
        self.started = threading.Event()
        self.release = threading.Event()
        self.threads: list[int] = []

    @property
    def calls(self) -> int:
        return len(self.threads)

    def __call__(self, arg: str) -> str:
        self.threads.append(threading.get_ident())
        self.started.set()
        assert self.release.wait(30), "o teste nunca soltou o /status"
        return self.answer


def admin(clock=None, **actions) -> TelegramAdmin:
    kw = {} if clock is None else {"clock": clock}
    return TelegramAdmin(bot_token="t", admin_chat_id=ADMIN, approval=None,
                         actions=actions, **kw)


class Chat:
    """O que o admin vê: as respostas, pela ordem em que chegaram."""

    def __init__(self, tg: TelegramAdmin):
        self.tg = tg
        self.replies: list[str] = []

    async def reply(self, text: str) -> None:
        self.replies.append(text)

    async def send(self, text: str, chat_id=ADMIN) -> None:
        await self.tg.handle_command(text, chat_id, self.reply)


async def until(happened, seconds: float = 20.0) -> None:
    """Esperar pelo que acontece noutra thread, sem parar o ciclo."""
    deadline = time.monotonic() + seconds
    while not happened():
        assert time.monotonic() < deadline, "nunca aconteceu"
        await asyncio.sleep(0.002)


def brake(log: list | None = None):
    def silence(arg: str) -> str:
        if log is not None:
            log.append(threading.get_ident())
        return PAUSED
    return silence


# --------------------------------------------------------------------------- #
# 1. O travão não espera                                                        #
# --------------------------------------------------------------------------- #


def test_silence_answers_at_once_while_a_status_is_still_running():
    status = Held()
    tg = admin(status=status, silence=brake())
    chat = Chat(tg)

    async def scenario():
        await chat.send("/status")                      # back at once, nothing to say yet
        assert chat.replies == []
        await until(status.started.is_set)              # running — and stuck
        asked = time.monotonic()
        await chat.send("/silence")
        took = time.monotonic() - asked
        # the brake ran and answered with the /status still stuck
        assert chat.replies == [PAUSED]
        assert status.calls == 1 and not status.release.is_set()
        assert took < 1.0
        status.release.set()
        await tg.drain()
        assert chat.replies == [PAUSED, "estado"]       # …and the /status is not lost

    asyncio.run(scenario())


def test_the_brake_can_be_pulled_again_and_again_under_the_same_status():
    status = Held()
    tg = admin(status=status, silence=brake(), resume=lambda arg: "▶️ resumed.")
    chat = Chat(tg)

    async def scenario():
        await chat.send("/status")
        await until(status.started.is_set)
        for text in ("/silence", "/resume", "/silence"):
            await chat.send(text)
        assert chat.replies == [PAUSED, "▶️ resumed.", PAUSED]
        status.release.set()
        await tg.drain()

    asyncio.run(scenario())


def test_a_search_that_never_answers_cannot_hold_the_brake():
    """De ponta a ponta com a linha verdadeira: as três medições presas
    dentro da pesquisa, ao mesmo tempo — e o /silence responde."""
    stuck = threading.Event()
    inside: list[int] = []

    class Hanging(Search):
        def _answer(self, text, **kw):
            inside.append(threading.get_ident())
            assert stuck.wait(30), "o teste nunca soltou a pesquisa"
            return super()._answer(text, **kw)

    w = wiring(market=Hanging(), deposit_chains=CHAINS)
    tg = admin(status=lambda arg: w.search_line(), silence=brake())
    chat = Chat(tg)

    async def scenario():
        await chat.send("/status")
        await until(lambda: len(inside) == 3)           # unfiltered, base, ethereum
        await chat.send("/silence")
        assert chat.replies == [PAUSED] and len(set(inside)) == 3
        stuck.set()
        await tg.drain()
        lines = chat.replies[1].split("\n")
        assert lines[0].startswith("pesquisa OpenSea, sem filtro: respondeu em ")
        assert lines[1].startswith("  por cadeia: base respondeu em ")

    asyncio.run(scenario())


def test_the_status_never_runs_on_the_loop_s_thread_and_the_brake_always_does():
    where: dict[str, int] = {}
    pulled: list[int] = []

    def status(arg: str) -> str:
        where["status"] = threading.get_ident()
        return "estado"

    tg = admin(status=status, silence=brake(pulled))
    chat = Chat(tg)

    async def scenario():
        loop_thread = threading.get_ident()
        await chat.send("/status")
        await tg.drain()
        await chat.send("/silence")
        assert where["status"] != loop_thread and pulled == [loop_thread]
        assert chat.replies == ["estado", PAUSED]

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 2. Todos os outros comandos: em linha, como sempre                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", sorted(TELEGRAM_COMMANDS - OFF_LOOP_COMMANDS))
def test_every_other_command_is_answered_before_the_handler_returns(name):
    ran: list[tuple[int, str]] = []

    def action(arg: str) -> str:
        ran.append((threading.get_ident(), arg))
        return f"fez {name}"

    tg = admin(**{name: action})
    chat = Chat(tg)

    async def scenario():
        await chat.send(f"/{name} 150")
        assert chat.replies == [f"fez {name}"]           # no drain: it is already there
        assert ran == [(threading.get_ident(), "150")]   # on the loop's thread, with its argument
        assert not tg._tasks and not tg._running         # noqa: SLF001

    asyncio.run(scenario())


def test_only_the_status_runs_off_the_loop_and_the_brake_never():
    """Acrescentar aqui um comando é pô-lo a correr ao mesmo tempo que o
    seguinte: só os que LÊEM. Mudar esta lista tem de ser de propósito."""
    assert OFF_LOOP_COMMANDS == {"status"}
    assert OFF_LOOP_COMMANDS <= TELEGRAM_COMMANDS
    assert not OFF_LOOP_COMMANDS & {"silence", "resume", "launch"}


def test_a_stranger_gets_one_word_and_starts_nothing():
    status = Held()
    tg = admin(status=status, silence=brake())
    chat = Chat(tg)

    async def scenario():
        await chat.send("/status", chat_id="666")
        await chat.send("/silence", chat_id="666")
        assert chat.replies == ["unauthorized", "unauthorized"]
        assert status.calls == 0 and not tg._tasks       # noqa: SLF001

    asyncio.run(scenario())


def test_an_unknown_command_reads_as_it_always_did():
    chat = Chat(admin())

    async def scenario():
        await chat.send("/frobnicate")
        await chat.send("")
        assert chat.replies == ["unknown command: /frobnicate", "no command"]

    asyncio.run(scenario())


@pytest.mark.parametrize("text, name, arg", [
    ("/launch 200", "launch", "200"),
    ("/STATUS", "status", ""),
    ("/harvest   manifold 150 ", "harvest", "manifold 150"),
    ("status", "status", ""),
])
def test_one_reading_of_a_command_for_the_route_and_for_the_lane(text, name, arg):
    """O que decide para onde vai o comando e o que decide se sai do ciclo
    lêem o texto da MESMA maneira — a que o encaminhamento sempre teve."""
    assert split_command(text) == (name, arg)
    seen: list[str] = []
    route_command(text, is_admin=True, actions={name: lambda a: seen.append(a) or "ok"})
    assert seen == [arg]


@pytest.mark.parametrize("text", ["", "/", "   "])
def test_no_command_is_no_command(text):
    assert split_command(text) == ("", "")
    assert route_command(text, is_admin=True, actions={}) == "no command"


def test_the_status_is_recognised_however_it_is_typed():
    status = Held()
    status.release.set()
    tg = admin(status=status)
    chat = Chat(tg)

    async def scenario():
        loop_thread = threading.get_ident()
        for text in ("/STATUS", "/status  agora", "/Status"):
            await chat.send(text)
            await tg.drain()
        assert status.calls == 3 and loop_thread not in status.threads

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 3. Um /status de cada vez                                                     #
# --------------------------------------------------------------------------- #


def test_a_second_status_is_told_to_wait_and_starts_nothing():
    status = Held()
    tg = admin(status=status)
    chat = Chat(tg)

    async def scenario():
        await chat.send("/status")
        await until(status.started.is_set)
        await chat.send("/status")
        await chat.send("/status")
        assert chat.replies == [WAIT, WAIT] and status.calls == 1
        status.release.set()
        await tg.drain()
        assert chat.replies == [WAIT, WAIT, "estado"]
        # …and once it has answered, the next one runs
        await chat.send("/status")
        await tg.drain()
        assert chat.replies[-1] == "estado" and status.calls == 2
        assert not tg._running                           # noqa: SLF001

    asyncio.run(scenario())


def test_a_status_stuck_for_two_minutes_no_longer_keeps_a_new_one_out():
    """Um /status que ninguém pudesse voltar a correr até ao próximo deploy
    era ele próprio uma avaria."""
    assert OFF_LOOP_STALE_S == 120.0
    now = [1000.0]
    old, new = Held("velho"), Held("novo")
    runs = iter([old, new])
    tg = admin(clock=lambda: now[0], status=lambda arg: next(runs)(arg))
    chat = Chat(tg)

    async def scenario():
        await chat.send("/status")
        await until(old.started.is_set)
        now[0] += OFF_LOOP_STALE_S - 0.5
        await chat.send("/status")
        assert chat.replies == [WAIT] and not new.started.is_set()
        now[0] += 0.5                                    # two minutes: taken for stuck
        await chat.send("/status")
        await until(new.started.is_set)
        # the old one ends at last: its answer still arrives, and it does not
        # take the newer one's mark with it — a third is still told to wait
        old.release.set()
        await until(lambda: "velho" in chat.replies)
        await chat.send("/status")
        assert chat.replies == [WAIT, "velho", WAIT]
        new.release.set()
        await tg.drain()
        assert chat.replies == [WAIT, "velho", WAIT, "novo"]
        assert not tg._running                           # noqa: SLF001

    asyncio.run(scenario())


def test_two_runs_are_never_taken_for_one_even_on_a_clock_that_repeats():
    """A marca de cada corrida é só dela, não a hora a que começou: com um
    relógio que dá duas vezes o mesmo valor, o antigo apagava a do novo."""
    ticks = iter([1000.0, 1000.0 + OFF_LOOP_STALE_S, 1000.0,      # start, stale?, restart
                  1000.0])                                         # the third: stale?
    old, new = Held("velho"), Held("novo")
    runs = iter([old, new])
    tg = admin(clock=lambda: next(ticks), status=lambda arg: next(runs)(arg))
    chat = Chat(tg)

    async def scenario():
        await chat.send("/status")
        await until(old.started.is_set)
        await chat.send("/status")                       # the first is taken for stuck
        await until(new.started.is_set)
        old.release.set()
        await until(lambda: "velho" in chat.replies)
        await chat.send("/status")                       # the new one still holds its mark
        assert chat.replies == ["velho", WAIT]
        new.release.set()
        await tg.drain()
        assert not tg._running                           # noqa: SLF001

    asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 4. Quando corre mal                                                           #
# --------------------------------------------------------------------------- #


def test_a_status_that_blows_up_says_so_with_the_type_only():
    calls: list[int] = []

    def status(arg: str) -> str:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError(SECRET)
        return "estado"

    tg = admin(status=status)
    chat = Chat(tg)

    async def scenario():
        await chat.send("/status")
        await tg.drain()
        assert chat.replies == ["⚠️ /status falhou (RuntimeError) — tenta outra vez"]
        assert "Quiet" not in chat.replies[0] and "example" not in chat.replies[0]
        await chat.send("/status")                       # and the next one is not kept out
        await tg.drain()
        assert chat.replies[-1] == "estado"

    asyncio.run(scenario())


def test_a_reply_that_cannot_be_sent_is_logged_by_type_and_frees_the_next(caplog):
    tg = admin(status=lambda arg: "estado")
    sent: list[str] = []

    async def broken(text: str) -> None:
        raise ConnectionError(SECRET)

    async def working(text: str) -> None:
        sent.append(text)

    async def scenario():
        with caplog.at_level("ERROR"):
            await tg.handle_command("/status", ADMIN, broken)
            await tg.drain()
        assert not tg._running and not tg._tasks         # noqa: SLF001
        await tg.handle_command("/status", ADMIN, working)
        await tg.drain()
        assert sent == ["estado"]

    asyncio.run(scenario())
    assert "reply to /status failed: ConnectionError" in caplog.text
    assert "Quiet" not in caplog.text and "example" not in caplog.text


# --------------------------------------------------------------------------- #
# 5. Pela biblioteca do Telegram, como em produção                              #
# --------------------------------------------------------------------------- #


def _update(bot, n: int, text: str, chat_id: int = int(ADMIN)):
    from telegram import Update
    return Update.de_json({
        "update_id": n,
        "message": {
            "message_id": n, "date": 0, "text": text,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": chat_id, "is_bot": False, "first_name": "p"},
            "entities": [{"type": "bot_command", "offset": 0,
                          "length": len(text.split()[0])}],
        }}, bot)


def _application(tg: TelegramAdmin, monkeypatch) -> tuple[object, list[str]]:
    """A Application verdadeira, com os handlers que a produção regista —
    sem rede: nunca se inicializa, e o envio da resposta é de mentira."""
    from telegram import Message, User

    sent: list[str] = []

    async def reply_text(self, text, *a, **kw):
        sent.append(text)

    monkeypatch.setattr(Message, "reply_text", reply_text)
    app = tg.build_application()
    app._initialized = True                              # noqa: SLF001
    app.bot._bot_user = User(id=1, first_name="b", is_bot=True,   # noqa: SLF001
                             username="fm_test_bot")
    return app, sent


def test_through_the_library_the_brake_overtakes_a_status_that_is_stuck(monkeypatch):
    """O mesmo, pelo caminho da produção: a biblioteca processa uma
    actualização de cada vez e só lê a seguinte quando o handler devolve."""
    pytest.importorskip("telegram")
    status = Held()
    tg = TelegramAdmin(bot_token="123:abc", admin_chat_id=ADMIN, approval=None,
                       actions={"status": status, "silence": brake()})
    app, sent = _application(tg, monkeypatch)

    async def scenario():
        await app.process_update(_update(app.bot, 1, "/status"))
        await until(status.started.is_set)
        await app.process_update(_update(app.bot, 2, "/silence"))
        assert sent == [PAUSED] and not status.release.is_set()
        status.release.set()
        await tg.drain()
        assert sent == [PAUSED, "estado"]

    asyncio.run(scenario())


def test_through_the_library_every_command_still_reaches_its_action(monkeypatch):
    pytest.importorskip("telegram")
    ran: list[tuple[str, str]] = []
    actions = {name: (lambda arg, name=name: ran.append((name, arg)) or f"fez {name}")
               for name in TELEGRAM_COMMANDS}
    tg = TelegramAdmin(bot_token="123:abc", admin_chat_id=ADMIN, approval=None,
                       actions=actions)
    app, sent = _application(tg, monkeypatch)
    names = sorted(TELEGRAM_COMMANDS)

    async def scenario():
        for n, name in enumerate(names, 1):
            await app.process_update(_update(app.bot, n, f"/{name} 7"))
            await tg.drain()
        # a stranger: the library hands it over, the router turns it away
        await app.process_update(_update(app.bot, 99, "/status", chat_id=666))

    asyncio.run(scenario())
    assert ran == [(name, "7") for name in names]
    assert sent == [f"fez {name}" for name in names] + ["unauthorized"]
