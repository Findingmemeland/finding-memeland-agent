"""Dry-run do jogo-alvo (soldadura 6/6) — corre no Mac, nada é publicado.

    python scripts/simulate_target_hunt.py                # todos os cenários, fakes
    python scripts/simulate_target_hunt.py happy 429      # só alguns
    python scripts/simulate_target_hunt.py happy --real-clues

Sem `--real-clues` tudo é falso e determinístico (0 chamadas, 0 rede): serve
para ver o JOGO — o hold a entrar e a sair, o void com o gateway em baixo, o
tecto acumulado, as respostas de formato. Com `--real-clues` o caminho feliz
usa o TargetClueEngine REAL (Anthropic, guarda de pesquisa desligada — só em
simulação; juiz de consistência LIGADO, é uma chamada por rascunho) sobre um token real do fixture (FND #1, "Ancient Future", Sarah
Zucker 2020), para leres os posts todos seguidos como um jogador os lê. É a
última vez que os vês antes de serem públicos (Opus, 09/09).

Cenários obrigatórios (Opus): 429, void, flap. Os outros: happy, shotgun,
content, resume. Sai com código 1 se algum cenário falhar.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from finding_memeland.target.clues import ClueGuardUnavailable
from finding_memeland.target.dryrun import (
    MANDATORY,
    SCENARIOS,
    TargetWorld,
    run_scenarios,
    synthetic_snapshot,
)

FIXTURE = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "target" / "gateway_0.json"
FND = "0x3b3ee1931dc30c1957379fac9aba94d1c48a5405"


def real_world_factory(verbose: bool):
    """Happy path over a REAL token (from the capture) with the REAL clue
    engine. The draw is seeded until it lands on that token — the fake
    judge/uniqueness say yes to everything, so the seed search is cheap."""
    from anthropic import Anthropic

    from finding_memeland.config import get_settings
    from finding_memeland.target.clues import TargetClueEngine
    from finding_memeland.target.refresh import content_id
    from finding_memeland.target.selector import metadata_hash
    from finding_memeland.target.snapshot import SnapshotEntry

    s = get_settings()
    if not s.anthropic_api_key:
        print("FAIL — ANTHROPIC_API_KEY em falta no .env (precisa dele para --real-clues)")
        sys.exit(2)
    meta = json.loads(FIXTURE.read_text())["body"]
    uri = "ipfs://QmcsepfMDFh2udUQWtvcnZeARNFFCPA1n2WRWUH1ysWv4W/metadata.json"
    real = SnapshotEntry(
        chain="ethereum", contract=FND, token_id=1,
        name="ancient future", name_onchain=str(meta["name"]),
        metadata=meta, metadata_sha256=metadata_hash(meta), platform="foundation",
        token_uri=uri, content_id=content_id(uri))
    snap = synthetic_snapshot()
    snap.entries.append(real)
    from finding_memeland.target.clues import AnthropicTruthJudge
    client = Anthropic(api_key=s.anthropic_api_key)
    engine = TargetClueEngine(client, s.anthropic_model,
                              search_guard=False,     # simulação: sem guarda de pesquisa
                              truth_judge=AnthropicTruthJudge(client, s.target_judge_model))
    description = (
        "Glowing white serif text on a black field, the letters warped and "
        "smeared by analog video feedback — a VHS tracking line drifts across "
        "the frame. Concentric rings pulse behind the words like a CRT afterimage. "
        "The palette is phosphor white, faint magenta and cyan fringes, deep "
        "black. It reads as a broadcast from a machine remembering itself."
    )
    for seed in range(400):
        w = TargetWorld(snapshot=snap, clue_engine=engine, verbose=verbose, seed=seed,
                        describe_image=lambda sealed: description)
        # peek the draw without launching: same rng path as prepare()
        probe = TargetWorld(snapshot=snap, seed=seed)
        if probe.orch._prepare(200).target.id() == real_id(real):
            return w
    print("FAIL — não encontrei uma seed que sorteie o token real em 400 tentativas")
    sys.exit(2)


def real_id(e) -> str:
    return f"{e.chain}:{e.contract.lower()}:{e.token_id}"


def main(argv: list[str]) -> int:
    real = "--real-clues" in argv
    names = [a for a in argv if a in SCENARIOS] or list(SCENARIOS)
    factory = None
    if real:
        if names != ["happy"]:
            print("--real-clues só faz sentido com o cenário 'happy' (chamadas reais).")
            names = ["happy"]
        factory = real_world_factory
    try:
        reports = run_scenarios(names, verbose=True, world_factory=factory)
    except ClueGuardUnavailable as e:
        # in production this is a HOLD (deadline frozen, operator called);
        # in the simulation it is the end of the run — say why, no traceback
        print(f"\nFAIL — um guarda nosso não conseguiu verificar (em produção: HOLD): {e}")
        return 1
    print("\n" + "=" * 72 + "\nRELATÓRIO\n" + "=" * 72)
    for rep in reports:
        print(rep.render())
    missing = [m for m in MANDATORY if m not in names]
    if missing:
        print(f"\n(cenários obrigatórios não corridos nesta invocação: {missing})")
    bad = [r.name for r in reports if not r.ok]
    print("\n" + ("TUDO VERDE" if not bad else f"FALHAS: {bad}"))
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
