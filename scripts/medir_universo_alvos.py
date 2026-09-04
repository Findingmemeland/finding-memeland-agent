#!/usr/bin/env python3
"""Medição do universo de alvos para a Opção A (redesign pós-Hunt #10) — v4.

Corre no Mac do Pedro (precisa de rede; stdlib apenas):

    cd finding-memeland-agent
    .venv/bin/python scripts/medir_universo_alvos.py

Lê do .env: BASE_RPC_URL (usar o dedicado/Alchemy — o público recusa getLogs
históricos) e RARIBLE_API_KEY (para o filtro de unicidade do nome).

Filtros DUROS de selecção (decisões de 03-04/09, Fable+Opus+Pedro):
  1. metadata legível (tokenURI resolve, JSON com name+image)
  2. nome com 2+ palavras a sério
  3. dono é EOA — eth_getCode(ownerOf) == 0x (corta lending vaults, escrow,
     fraccionamento; substitui a dormência, ideia do Opus)
  4. nome ÚNICO no marketplace — a pesquisa devolve exactamente ESTE token
     (corta edições de colecção com nome partilhado; ideia do Pedro)
Dormência (sem Transfer há N dias): despromovida a desempate — é MEDIDA e
reportada nos candidatos finais, mas não gateia.

O gate verdadeiro (Opus, 04/09): a taxa de ESCREVIBILIDADE sobre os candidatos
que passam tudo — >2% do universo efectivo >1M = verde; <1% = outra volta.
Este script produz o universo e os candidatos; o testar_escrevibilidade.py
fecha o gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import ssl
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()

TRANSFER = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO32 = "0x" + "00" * 32
SEL_TOKENURI = "0xc87b56dd"  # tokenURI(uint256)
SEL_OWNEROF = "0x6352211e"   # ownerOf(uint256)
BLOCKS_PER_DAY = 43200       # Base: ~2 s/bloco
IPFS_GATEWAYS = ["https://ipfs.io/ipfs/", "https://cloudflare-ipfs.com/ipfs/"]
UA = {"User-Agent": "fml-universe-probe/1.0"}
RARIBLE_BASE = "https://api.rarible.org/v0.1"


def read_env(key: str) -> str | None:
    try:
        with open(".env", encoding="utf-8") as f:
            for line in f:
                if line.startswith(key + "="):
                    v = line.split("=", 1)[1].strip()
                    if v:
                        return v
    except OSError:
        pass
    return None


class Rpc:
    def __init__(self, url: str):
        self.url = url
        self._id = 0
        self.calls = 0

    def __call__(self, method: str, params: list, *, tries: int = 4):
        self._id += 1
        body = json.dumps(
            {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        ).encode()
        delay = 0.8
        for attempt in range(tries):
            try:
                req = urllib.request.Request(
                    self.url, data=body,
                    headers={"Content-Type": "application/json", **UA},
                )
                with urllib.request.urlopen(req, timeout=25, context=_SSL_CTX) as r:
                    out = json.load(r)
                self.calls += 1
                if "error" in out:
                    raise RuntimeError(out["error"].get("message", "rpc error"))
                return out["result"]
            except Exception:  # noqa: BLE001 — retry tudo, é uma sonda
                if attempt == tries - 1:
                    raise
                time.sleep(delay)
                delay *= 2
        raise RuntimeError("unreachable")


def hex_int(x: str) -> int:
    return int(x, 16)


def topic_for_token(token_id: int) -> str:
    return "0x" + token_id.to_bytes(32, "big").hex()


def decode_string_result(hexdata: str) -> str | None:
    if not hexdata or hexdata == "0x":
        return None
    raw = bytes.fromhex(hexdata[2:])
    if len(raw) < 64:
        return None
    try:
        offset = int.from_bytes(raw[0:32], "big")
        length = int.from_bytes(raw[offset:offset + 32], "big")
        return raw[offset + 32: offset + 32 + length].decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return None


def fetch_url(url: str) -> bytes | None:
    try:
        req = urllib.request.Request(url, headers=UA)
        with urllib.request.urlopen(req, timeout=25, context=_SSL_CTX) as r:
            return r.read(400_000)
    except Exception:  # noqa: BLE001
        return None


def resolve_token_uri(uri: str) -> dict | None:
    if not uri:
        return None
    uri = uri.strip().rstrip("\x00")
    try:
        if uri.startswith("data:"):
            import base64
            head, payload = uri.split(",", 1)
            if ";base64" in head:
                return json.loads(base64.b64decode(payload))
            return json.loads(urllib.parse.unquote(payload))
        if uri.startswith("ipfs://"):
            path = uri[len("ipfs://"):]
            path = path[len("ipfs/"):] if path.startswith("ipfs/") else path
            for gw in IPFS_GATEWAYS:
                data = fetch_url(gw + path)
                if data:
                    return json.loads(data)
            return None
        if uri.startswith(("http://", "https://")):
            data = fetch_url(uri)
            return json.loads(data) if data else None
    except Exception:  # noqa: BLE001
        return None
    return None


_WORD = re.compile(r"[A-Za-zÀ-ÿ]{2,}")

# serial de cauda: '#9278', ' 12684', '- 27132', 'No. 7', 'nº 3', repetido
_SERIAL_TAIL = re.compile(
    r"[\s\-–—_.:]*(?:#|n[oº]\.?\s*)?\d{1,10}\s*$", re.IGNORECASE)


def normalize_name(name: str) -> str:
    """Nome-base sem serial de cauda: 'Tiny Punk #9278' → 'Tiny Punk',
    'healing lucid glow 12684' → 'healing lucid glow'. É sobre o nome-base
    que se escrevem as pistas e se exige unicidade; se remover tudo,
    devolve o original."""
    base = name.strip()
    while True:
        new = _SERIAL_TAIL.sub("", base).strip()
        if new == base or not new:
            break
        base = new
    return base or name.strip()


def name_ok(name: str | None) -> bool:
    if not name:
        return False
    return len(_WORD.findall(name)) >= 2


def owner_is_eoa(rpc: Rpc, contract: str, token_id: int) -> bool | None:
    """ownerOf + eth_getCode: True = conta normal; False = contrato (custódia
    estranha — descarta); None = não determinável."""
    try:
        data = rpc("eth_call", [{
            "to": contract,
            "data": SEL_OWNEROF + token_id.to_bytes(32, "big").hex(),
        }, "latest"], tries=2)
        if not data or len(data) < 66:
            return None
        owner = "0x" + data[-40:]
        code = rpc("eth_getCode", [owner, "latest"], tries=2)
        return code in ("0x", "0x0", None) or code == ""
    except Exception:  # noqa: BLE001
        return None


def name_is_unique(api_key: str, name: str, contract: str,
                   token_id: int) -> bool | None:
    """Pesquisa Rarible (formato medido em relic_findability.py, 25/08:
    header X-API-KEY, filtro fullText). Recebe o NOME-BASE (já normalizado);
    cada resultado é normalizado antes de comparar, portanto 'Tiny Punk'
    contra uma colecção de Tiny Punk #1..#N dá vários acertos → não-único.
    Único = o ÚNICO item cujo nome-base bate é este contract:tokenId.
    None = não verificável (sem chave / API em baixo)."""
    if not api_key:
        return None
    body = json.dumps({
        "size": 25,
        "filter": {"fullText": {"text": name}, "blockchains": ["BASE"]},
    }).encode()
    req = urllib.request.Request(
        f"{RARIBLE_BASE}/items/search", data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json",
                 "X-API-KEY": api_key, **UA},
    )
    for attempt in (1, 2, 3):
        try:
            with urllib.request.urlopen(req, timeout=25, context=_SSL_CTX) as r:
                out = json.load(r)
            break
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                time.sleep(2.0 * attempt)
                continue
            return None
        except Exception:  # noqa: BLE001
            return None
    target = f"BASE:{contract.lower()}:{token_id}"
    want = name.strip().casefold()
    exact = []
    for item in out.get("items", []) or []:
        raw = str(((item.get("meta") or {}).get("name")) or "")
        if normalize_name(raw).casefold() == want:
            exact.append(str(item.get("id", "")).lower())
    return len(exact) == 1 and exact[0] == target.lower()


def token_is_quiet(rpc: Rpc, contract: str, token_id: int,
                   from_block: int, to_block: int):
    """Informativo (desempate): (True|False|None, nota)."""
    t3 = topic_for_token(token_id)
    span = to_block - from_block + 1
    last_err = ""
    for size in (span, 1_000_000, 250_000):
        if size > span:
            continue
        start, failed = from_block, False
        while start <= to_block:
            end = min(start + size - 1, to_block)
            try:
                logs = rpc("eth_getLogs", [{
                    "fromBlock": hex(start), "toBlock": hex(end),
                    "address": contract,
                    "topics": [TRANSFER, None, None, t3],
                }], tries=2)
            except Exception as e:  # noqa: BLE001
                failed, last_err = True, str(e)[:80]
                break
            if logs:
                return False, "activo"
            start = end + 1
        if not failed:
            return True, "quieto"
    return None, f"indeterminado ({last_err})"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rpc", default=None)
    ap.add_argument("--blocks", type=int, default=120)
    ap.add_argument("--tokens", type=int, default=120)
    ap.add_argument("--min-age-days", type=int, default=180)
    ap.add_argument("--quiet-days", type=int, default=180)
    ap.add_argument("--out", default="candidatos_alvo.json")
    ap.add_argument("--seed", type=int, default=None)
    args = ap.parse_args()

    rng = random.Random(args.seed)
    rpc = Rpc(args.rpc or read_env("BASE_RPC_URL") or "https://mainnet.base.org")
    rarible_key = read_env("RARIBLE_API_KEY") or ""
    print(f"RPC: {rpc.url}")
    print(f"Rarible key: {'sim' if rarible_key else 'NÃO — unicidade fica por verificar'}")

    head = hex_int(rpc("eth_blockNumber", []))
    cutoff = head - args.min_age_days * BLOCKS_PER_DAY
    quiet_from = head - args.quiet_days * BLOCKS_PER_DAY
    lo = 1_500_000
    window = cutoff - lo
    print(f"head={head:,}  janela elegível: [{lo:,}, {cutoff:,}]  ({window:,} blocos)")

    # ---- fase 1: taxa de mints ERC-721 por bloco -------------------------
    per_block: list[int] = []
    pool: list[tuple[str, int]] = []
    for i in range(args.blocks):
        b = rng.randrange(lo, cutoff)
        try:
            logs = rpc("eth_getLogs", [{
                "fromBlock": hex(b), "toBlock": hex(b),
                "topics": [TRANSFER, ZERO32],
            }])
        except Exception as e:  # noqa: BLE001
            print(f"  bloco {b:,}: getLogs falhou ({e}) — ignorado")
            continue
        mints = [(l["address"], hex_int(l["topics"][3]))
                 for l in logs if len(l.get("topics", [])) == 4]
        per_block.append(len(mints))
        seen = set()
        for c, t in mints:      # 1 token por (bloco, contrato): airdrops não dominam
            if c not in seen:
                seen.add(c)
                pool.append((c, t))
        if (i + 1) % 20 == 0:
            print(f"  fase 1: {i + 1}/{args.blocks} blocos…")

    if not per_block:
        print("Sem dados — RPC inutilizável.")
        return 2
    mean, med = statistics.fmean(per_block), statistics.median(per_block)
    total_mean, total_med = int(mean * window), int(med * window)
    print(f"\nmints ERC-721/bloco: média {mean:.2f} | mediana {med:.1f}")
    print(f"total estimado na janela: ~{total_med:,} (mediana) a ~{total_mean:,} (média)")

    # ---- fase 2: filtros duros ------------------------------------------
    rng.shuffle(pool)
    sub = pool[: args.tokens]
    n = len(sub)
    ok_meta = ok_name = ok_eoa = ok_uniq = uniq_untested = 0
    candidates = []
    for j, (contract, token_id) in enumerate(sub, 1):
        if j % 20 == 0:
            print(f"  fase 2: {j}/{n} tokens…")
        try:
            data = rpc("eth_call", [{
                "to": contract,
                "data": SEL_TOKENURI + token_id.to_bytes(32, "big").hex(),
            }, "latest"])
            uri = decode_string_result(data)
            meta = resolve_token_uri(uri) if uri else None
        except Exception:  # noqa: BLE001
            meta = None
        if not (isinstance(meta, dict) and meta.get("image")):
            continue
        ok_meta += 1
        name = str(meta.get("name") or "").strip()
        base = normalize_name(name)
        if not name_ok(base):
            continue
        ok_name += 1
        if owner_is_eoa(rpc, contract, token_id) is not True:
            continue
        ok_eoa += 1
        uniq = name_is_unique(rarible_key, base, contract, token_id)
        if uniq is None:
            uniq_untested += 1     # sem chave/API: passa com aviso, marcado
        elif not uniq:
            continue
        else:
            ok_uniq += 1
        quiet, quiet_note = token_is_quiet(rpc, contract, token_id, quiet_from, head)
        candidates.append({
            "chain": "base",
            "contract": contract,
            "tokenId": token_id,
            "name": base,                 # nome-base: o que as pistas cifram
            "name_onchain": name,         # nome completo no metadata
            "description": str(meta.get("description") or "")[:600],
            "image": str(meta.get("image") or ""),
            "metadata_sha256": hashlib.sha256(
                json.dumps(meta, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest(),
            "name_unique": uniq,          # True | None (não verificado)
            "quiet_180d": quiet, "quiet_note": quiet_note,   # desempate, informativo
        })

    if n == 0:
        print("Sub-amostra vazia — aumenta --blocks.")
        return 2

    r_name = ok_name / n
    r_eoa = ok_eoa / n
    passed = ok_uniq + uniq_untested
    r_all = passed / n
    print(f"\nfiltros duros ({n} tokens): metadata {ok_meta / n:.0%} → "
          f"nome-base 2+ palavras {r_name:.0%} → dono EOA {r_eoa:.0%} → "
          f"nome-base único {r_all:.0%}"
          + (f" ({uniq_untested} sem verificação de unicidade)" if uniq_untested else ""))

    est_med, est_mean = int(total_med * r_all), int(total_mean * r_all)
    print("\n================= RESULTADO =================")
    print(f"universo on-chain após filtros duros: ~{est_med:,} a ~{est_mean:,}")
    if uniq_untested:
        print("⚠️ unicidade não verificada em parte da amostra (falta RARIBLE_API_KEY?) "
              "— o número acima é um tecto, não uma estimativa.")
    print("O GATE é a escrevibilidade: universo efectivo = número acima × "
          "taxa do testar_escrevibilidade.py. Fasquia em re-derivação "
          "(Rederivacao_Fasquia_OpcaoA.md) — a antiga 1M/2% assumia o "
          "modelo de colheita, não o de adivinhação.")
    print(f"chamadas RPC: {rpc.calls}")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(candidates, f, ensure_ascii=False, indent=1)
    print(f"\n{len(candidates)} candidatos → {args.out} (input do testar_escrevibilidade.py)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
