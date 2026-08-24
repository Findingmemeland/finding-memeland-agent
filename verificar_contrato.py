"""Verificar contracts/RelicNFT.json contra o contrato que já está na cadeia.

    .venv/bin/python verificar_contrato.py

Porque isto é uma verificação a sério e não um palpite: o bytecode de CRIAÇÃO
contém, lá dentro, o bytecode de RUNTIME que fica no contrato depois do deploy.
O Uncle Pump foi deployado com este mesmo contrato, e o runtime dele está
público na Base. Se o runtime da cadeia aparecer dentro do que colaste, é o
mesmo contrato, carácter a carácter — e uma cópia truncada não passa.

Verifica quatro coisas:
  1. o JSON é válido e tem abi + bytecode
  2. o construtor tem os 5 argumentos string na ordem certa
  3. o bytecode parece bytecode de criação (começa e acaba como deve)
  4. o runtime que está na cadeia está contido no bytecode do ficheiro
"""

from __future__ import annotations

import sys

sys.path.insert(0, "src")

# O relic mintado à mão em 24/08, deployado com este contrato.
REFERENCE_CONTRACT = "0x692f42DD372AE65F696C7E53083A2C915cB7C8ec"
EXPECTED_CTOR = ["name_", "symbol_", "description_", "imageURI_", "artist_"]


def main() -> int:
    ok = True

    try:
        from finding_memeland.persona.relic_mint import load_contract_artifact

        abi, bytecode = load_contract_artifact()
    except Exception as e:  # noqa: BLE001
        print(f"⛔ não consegui ler o artefacto: {e}")
        return 1
    print(f"✅ JSON válido — abi com {len(abi)} entradas, bytecode com {len(bytecode)} chars")

    ctor = next((e for e in abi if e.get("type") == "constructor"), None)
    if ctor is None:
        print("⛔ a ABI não tem construtor — copiaste o ficheiro errado?")
        return 1
    names = [i.get("name") for i in ctor.get("inputs", [])]
    types = [i.get("type") for i in ctor.get("inputs", [])]
    if names != EXPECTED_CTOR or types != ["string"] * 5:
        print(f"⛔ construtor inesperado: {list(zip(names, types))}")
        print(f"   esperado: {EXPECTED_CTOR}, todos string")
        ok = False
    else:
        print("✅ construtor com os 5 argumentos string na ordem certa")

    body = bytecode[2:] if bytecode.startswith("0x") else bytecode
    if not body.startswith("6080604052"):
        print("⛔ não começa como bytecode de criação (6080604052) — cópia truncada?")
        ok = False
    elif len(body) % 2:
        print("⛔ número ímpar de caracteres hex — a cópia perdeu um carácter")
        ok = False
    else:
        print("✅ formato de bytecode de criação")

    # --- a verificação que interessa -------------------------------------
    try:
        from web3 import Web3

        from finding_memeland.config import Settings

        w3 = Web3(Web3.HTTPProvider(Settings().base_rpc_url))
        onchain = w3.eth.get_code(Web3.to_checksum_address(REFERENCE_CONTRACT)).hex()
    except Exception as e:  # noqa: BLE001
        print(f"\n⚠️  não consegui ler a cadeia ({e!r}) — as verificações acima valem,")
        print("   mas a comparação com o contrato real não foi feita.")
        return 0 if ok else 1

    onchain = onchain[2:] if onchain.startswith("0x") else onchain
    if not onchain:
        print(f"\n⛔ não há código em {REFERENCE_CONTRACT} — endereço errado?")
        return 1

    print(f"\nruntime na cadeia: {len(onchain)} chars")
    if onchain.lower() in body.lower():
        print("✅ O RUNTIME DA CADEIA ESTÁ DENTRO DO TEU BYTECODE.")
        print("   É o mesmo contrato que mintou o Uncle Pump, verbatim.")
    else:
        print("⛔ o runtime da cadeia NÃO aparece no bytecode colado.")
        print("   Ou a cópia está incompleta, ou é de outra compilação.")
        ok = False

    print("\n" + ("✅ TUDO OK — podes mintar." if ok else "⛔ NÃO uses este ficheiro."))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
