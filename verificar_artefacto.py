"""Confirma que o contracts/RelicNFT.json recompilado serve para mintar.

O construtor passou de 5 para 6 argumentos (auditoria 2026-08-26, P0-1). Se o
artefacto for o antigo, o mint só rebenta na altura de assinar a transacção — ou
seja, com gás gasto e uma carteira queimada. Este script apanha isso em segundo.

    .venv/bin/python verificar_artefacto.py

Só lê ficheiros. Não toca na chain.
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")

ESPERADO = [
    ("name_", "string"),
    ("symbol_", "string"),
    ("description_", "string"),
    ("imageURI_", "string"),
    ("attributes_", "string"),
    ("provenanceHash_", "bytes32"),
]


def main() -> int:
    from finding_memeland.persona.relic_mint import load_contract_artifact

    try:
        abi, bytecode = load_contract_artifact()
    except Exception as e:  # noqa: BLE001
        print(f"✗ não consegui ler o artefacto: {e}")
        return 1

    ok = True

    ctor = next((e for e in abi if e.get("type") == "constructor"), None)
    if ctor is None:
        print("✗ o ABI não tem construtor")
        return 1

    obtido = [(i.get("name"), i.get("type")) for i in ctor.get("inputs", [])]
    if obtido == ESPERADO:
        print("✓ construtor com os 6 argumentos certos, pela ordem certa")
    else:
        ok = False
        print("✗ construtor errado — o artefacto é o ANTIGO ou compilaste outra versão")
        print("   esperado:", ESPERADO)
        print("   obtido:  ", obtido)

    if any(e.get("name") == "provenanceHash" for e in abi):
        print("✓ getter provenanceHash presente (é o que garante bytecode único)")
    else:
        ok = False
        print("✗ falta o getter provenanceHash — sem ele o optimizer pode ter")
        print("   descartado o immutable, e o bytecode volta a ser igual em todas")

    if isinstance(bytecode, str) and bytecode.startswith("0x") and len(bytecode) > 1000:
        print(f"✓ bytecode presente ({len(bytecode)} caracteres)")
    else:
        ok = False
        print(f"✗ bytecode com aspecto errado: {str(bytecode)[:60]!r}")

    print("\n" + ("TUDO OK — podes commitar e fazer push." if ok else
                  "NÃO COMMITES. Recompila e volta a correr."))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
