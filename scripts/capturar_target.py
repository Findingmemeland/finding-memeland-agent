"""Captura CRUA para os adaptadores do jogo-alvo (soldadura 5/6).

Regra do projecto: capturar cru → parser offline → run. Nenhum parser de
`target/adapters.py` se escreve contra documentação; escreve-se contra o que
ESTE script gravou. Corre no Mac do Pedro (a cloud e a VM não chegam a estes
hosts), com o .env do agente:

    python scripts/capturar_target.py                # tudo
    python scripts/capturar_target.py rpc rarible    # só alguns
    python scripts/capturar_target.py opensea        # OpenSea em vez da Rarible?
    python scripts/capturar_target.py foundation https://foundation.app/@x/y/1

Escreve em tests/fixtures/target/<nome>.json — cada ficheiro tem `_meta`
(url SEM segredos, status, data) e `body` (texto cru, ou o JSON decodificado
quando decodifica). Chaves de API nunca entram nos ficheiros: o host da RPC
é gravado, a query string não.

O que se mede, e porquê:
  rpc         eth_getLogs Transfer-from-0x0 de UM bloco da era 2021 (o
              fetch_mints da descoberta + a forma do canário), eth_call
              tokenURI/ownerOf de um token conhecido, eth_getCode de um
              contrato e de uma EOA — e o mesmo tokenURI via RPCs PÚBLICAS,
              para a lista de rotação do LiveCheck (nunca a nossa chave).
  gateways    o mesmo CID em gateways IPFS públicos — lista de rotação.
  rarible     GET /items/{CHAIN}:{contract}:{tid} em várias cadeias (a sonda
              de cadeia do resolvedor: link sem cadeia → em que cadeia
              existe?), incluindo a forma do 404.
  opensea     search por texto (com/sem cadeia) + GET do item por cadeia —
              mede se a OpenSea cobre os três papéis da Rarible (10/09).
  superrare   HTML da página /artwork/eth/<contract>/<tokenId> (registo; o
              URL já traz cadeia+contrato+tokenId, não precisa de parser).
  foundation  nada — o marketplace fechou (06/09/2026); ver FOUNDATION_URLS.
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

try:
    import certifi

    _SSL = ssl.create_default_context(cafile=certifi.where())
except Exception:  # noqa: BLE001
    _SSL = ssl.create_default_context()

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "target"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# Contrato partilhado da Foundation (FND). O #42 NÃO EXISTE (medido 06/09:
# 'URI query for nonexistent token' — queimado ou nunca cunhado), por isso o
# script SONDA ids até encontrar um que resolva e usa esse para tokenURI/
# ownerOf/gateways; o revert do #42 fica gravado como a forma do 'burn'.
FND = "0x3B3ee1931Dc30C1957379FAC9aba94D1C48a5405"
FND_TID = 42
FND_PROBE_IDS = range(1, 400)
# vitalik.eth: medido 06/09 com código 0xef0100… (delegação EIP-7702) — já
# não é a forma "vazia". Fica (é a forma 7702); o 0x…dEaD dá o vazio puro.
EOA = "0xd8dA6BF26964aF9D7eEd9e03E53415D37aA96045"
EOA_PLAIN = "0x000000000000000000000000000000000000dEaD"
# Bloco da era 2021 com mints (Ago 2021). Serve para medir a FORMA da
# resposta; o canary_block/canary_mints da época mede-se à parte, com
# contagem, sobre o bloco que se fixar.
ERA_BLOCK = 12_965_000
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
ZERO_TOPIC = "0x" + "0" * 64
# CID público conhecido (metadata JSON de um token Foundation — qualquer CID
# estável serve; o objectivo é a lista de gateways que respondem).
CID = "QmSiuJazyPgzAVqBiW3LMNdjAG4uaZFqzMwzU2kGS2KmCN"

# Medido 06/09: llamarpc 521 (em baixo), cloudflare-eth -32603 opaco (revert
# indistinguível de avaria — excluído da rotação), ankr exige chave;
# publicnode, drpc, 1rpc devolvem o revert com forma completa. Os restantes
# são candidatos a medir.
PUBLIC_RPCS = [
    "https://ethereum.publicnode.com",
    "https://eth.drpc.org",
    "https://1rpc.io/eth",
    "https://rpc.flashbots.net",
    "https://eth.merkle.io",
    "https://rpc.mevblocker.io",
    "https://eth-pokt.nodies.app",
    "https://eth.llamarpc.com",
    "https://cloudflare-eth.com",
    "https://rpc.ankr.com/eth",
]
# Medido 06/09: ipfs.io, dweb.link, w3s.link, nftstorage.link respondem 403
# "Just a moment…" (desafio JS da Cloudflare a não-browsers); cloudflare-ipfs
# já não resolve em DNS; só gateway.pinata.cloud serviu. Os restantes são
# candidatos a medir — a rotação genérica precisa de MAIS do que um.
GATEWAYS = [
    "https://gateway.pinata.cloud/ipfs/",
    "https://ipfs.4everland.io/ipfs/",
    "https://cf-ipfs.com/ipfs/",
    "https://gateway.ipfs.io/ipfs/",
    "https://ipfs.eth.aragon.network/ipfs/",
    "https://hardbin.com/ipfs/",
    "https://ipfs.filebase.io/ipfs/",
    "https://trustless-gateway.link/ipfs/",
    "https://ipfs.io/ipfs/",
    "https://dweb.link/ipfs/",
    "https://w3s.link/ipfs/",
    "https://nftstorage.link/ipfs/",
    "https://cloudflare-ipfs.com/ipfs/",
]
RARIBLE_CHAINS = ["ETHEREUM", "POLYGON", "BASE", "ARBITRUM", "OPTIMISM", "ZORA"]

# Foundation (o marketplace) FECHOU — comunicado no site, 06/09/2026: "we
# have made the decision not to resume operations"; os NFTs ficam na chain
# (não-custodial) e o gateway IPFS deles fica de pé até Abril de 2027. Não há
# página para capturar; o contrato FND continua a ser fonte da época 1 e os
# CIDs dele têm de resolver noutros gateways — é isso que `gateways` mede.
FOUNDATION_URLS: list[str] = []
# SuperRare: forma ACTUAL do URL (Pedro, 06/09) — cadeia no path, estrutural,
# claim.parse_link lê sem resolvedor. Captura-se o HTML só para o registo.
SUPERRARE_URLS = [
    "https://superrare.com/artwork/eth/0x63b34473C297CC2eA487ceA13871f0D4ce8b2c86/167",
]


def _env(name: str) -> str:
    v = os.environ.get(name, "")
    if v:
        return v
    env = Path(".env")
    if env.exists():
        for line in env.read_text().splitlines():
            if line.startswith(name + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _scrub(url: str) -> str:
    """Host + path, nunca a query (chaves em URL de RPC)."""
    p = urllib.parse.urlsplit(url)
    path = p.path
    # chaves Alchemy vivem no path (/v2/<key>): corta o último segmento
    if "alchemy" in p.netloc and path.count("/") >= 2:
        path = path.rsplit("/", 1)[0] + "/<key>"
    return f"{p.scheme}://{p.netloc}{path}"


def _call(url: str, *, body: dict | None = None, headers: dict | None = None,
          timeout: int = 30) -> tuple[int, str]:
    st, tx, _ = _call_h(url, body=body, headers=headers, timeout=timeout)
    return st, tx


_RATE_HEADERS = ("x-ratelimit-limit", "x-ratelimit-remaining", "x-ratelimit-reset",
                 "retry-after")


def _call_h(url: str, *, body: dict | None = None, headers: dict | None = None,
            timeout: int = 30) -> tuple[int, str, dict]:
    """Como `_call`, mas devolve também os cabeçalhos de quota (só esses —
    nunca o resto, que pode ecoar a chave)."""
    data = json.dumps(body).encode() if body is not None else None
    h = {"User-Agent": UA, "Accept": "application/json, text/html;q=0.9, */*;q=0.8"}
    if body is not None:
        h["Content-Type"] = "application/json"
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h,
                                 method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_SSL) as r:
            rate = {k: r.headers.get(k) for k in _RATE_HEADERS if r.headers.get(k)}
            return r.status, r.read().decode("utf-8", "replace"), rate
    except urllib.error.HTTPError as e:
        rate = {k: e.headers.get(k) for k in _RATE_HEADERS if e.headers.get(k)}
        return e.code, e.read().decode("utf-8", "replace"), rate
    except Exception as e:  # noqa: BLE001
        return 0, f"{type(e).__name__}: {e}", {}


def _save(name: str, url: str, status: int, text: str, **extra) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    try:
        body: object = json.loads(text)
    except ValueError:
        body = text
    doc = {"_meta": {"url": _scrub(url), "status": status,
                     "captured_at": datetime.now(timezone.utc).isoformat(),
                     **extra},
           "body": body}
    (OUT / f"{name}.json").write_text(json.dumps(doc, ensure_ascii=False, indent=1))
    size = len(text)
    print(f"  {name:34} {status:>3}  {size:>8}B  {_scrub(url)}")


def _rpc(url: str, method: str, params: list) -> tuple[int, str]:
    return _call(url, body={"jsonrpc": "2.0", "id": 1, "method": method,
                            "params": params})


def _selector_call(url: str, to: str, data: str) -> tuple[int, str]:
    return _rpc(url, "eth_call", [{"to": to, "data": data}, "latest"])


def _abi_uint(n: int) -> str:
    return f"{n:064x}"


TOKEN_URI = "0xc87b56dd"     # tokenURI(uint256)
OWNER_OF = "0x6352211e"      # ownerOf(uint256)


def cap_rpc() -> None:
    url = _env("ETH_RPC_URL")
    if not url:
        print("  ETH_RPC_URL em falta — salto a secção rpc")
        return
    st, tx = _rpc(url, "eth_blockNumber", [])
    _save("rpc_block_number", url, st, tx)
    st, tx = _rpc(url, "eth_getLogs", [{
        "fromBlock": hex(ERA_BLOCK), "toBlock": hex(ERA_BLOCK),
        "topics": [TRANSFER_TOPIC, ZERO_TOPIC]}])
    _save("rpc_getlogs_mints_one_block", url, st, tx, block=ERA_BLOCK)
    # a mesma janela com 11 blocos: a forma do ERRO do tecto (Alchemy free = 10)
    st, tx = _rpc(url, "eth_getLogs", [{
        "fromBlock": hex(ERA_BLOCK), "toBlock": hex(ERA_BLOCK + 10),
        "topics": [TRANSFER_TOPIC, ZERO_TOPIC]}])
    _save("rpc_getlogs_11_blocks", url, st, tx, block=ERA_BLOCK)
    # token que não existe (#42, medido): a forma do revert (burn/inexistente)
    st, tx = _selector_call(url, FND, TOKEN_URI + _abi_uint(FND_TID))
    _save("rpc_tokenuri_revert", url, st, tx, contract=FND, token_id=FND_TID)
    # um token que EXISTE: sonda ids até o tokenURI resolver
    live_id = None
    for cand in FND_PROBE_IDS:
        st, tx = _selector_call(url, FND, TOKEN_URI + _abi_uint(cand))
        if st == 200 and '"result"' in tx and '"error"' not in tx:
            live_id = cand
            break
    if live_id is None:
        print("  nenhum token FND resolveu nos ids sondados — tokenURI/ownerOf saltados")
    else:
        tid = _abi_uint(live_id)
        _save("rpc_tokenuri", url, st, tx, contract=FND, token_id=live_id)
        st, tx = _selector_call(url, FND, OWNER_OF + tid)
        _save("rpc_ownerof", url, st, tx, contract=FND, token_id=live_id)
    st, tx = _rpc(url, "eth_getCode", [FND, "latest"])
    _save("rpc_getcode_contract", url, st, tx)
    st, tx = _rpc(url, "eth_getCode", [EOA, "latest"])
    _save("rpc_getcode_eoa", url, st, tx, note="vitalik.eth — 7702 em 06/09")
    st, tx = _rpc(url, "eth_getCode", [EOA_PLAIN, "latest"])
    _save("rpc_getcode_eoa_plain", url, st, tx, note="0x…dEaD — sem código")
    print("  -- RPCs públicas (rotação do LiveCheck):")
    probe_tid = _abi_uint(live_id or FND_TID)
    for i, pub in enumerate(PUBLIC_RPCS):
        st, tx = _selector_call(pub, FND, TOKEN_URI + probe_tid)
        _save(f"pubrpc_{i}_tokenuri", pub, st, tx, contract=FND,
              token_id=live_id or FND_TID)


def _cid_from_capture() -> str:
    """The live FND token's tokenURI CID, if `rpc` captured it — the gateways test
    then measures whether FOUNDATION's content resolves elsewhere (their
    gateway dies April 2027). Falls back to the fixed CID."""
    try:
        doc = json.loads((OUT / "rpc_tokenuri.json").read_text())
        data = doc["body"]["result"]
        raw = bytes.fromhex(data[2:])
        off = int.from_bytes(raw[:32], "big")
        ln = int.from_bytes(raw[off:off + 32], "big")
        uri = raw[off + 32:off + 32 + ln].decode("utf-8", "replace")
        for marker in ("ipfs://", "/ipfs/"):
            if marker in uri:
                return uri.split(marker, 1)[1].strip("/")
        return uri if uri.startswith(("Qm", "baf")) else CID
    except Exception:  # noqa: BLE001
        return CID


def cap_gateways() -> None:
    cid = _cid_from_capture()
    print(f"  CID: {cid[:16]}… ({'do token FND vivo' if cid != CID else 'fixo'})")
    for i, gw in enumerate(GATEWAYS):
        st, tx = _call(gw + cid, timeout=25)
        _save(f"gateway_{i}", gw + cid, st, tx[:4000], cid=cid, truncated=len(tx) > 4000)


def cap_rarible() -> None:
    key = _env("RARIBLE_API_KEY")
    headers = {"X-API-KEY": key} if key else {}
    if not key:
        print("  RARIBLE_API_KEY em falta — tento sem chave (pode dar 401/403)")
    for ch in RARIBLE_CHAINS:
        url = f"https://api.rarible.org/v0.1/items/{ch}:{FND.lower()}:{FND_TID}"
        st, tx = _call(url, headers=headers)
        _save(f"rarible_item_{ch.lower()}", url, st, tx, chain=ch)
    # a forma do 400 para uma cadeia que a Rarible não conhece
    url = f"https://api.rarible.org/v0.1/items/NOPE:{FND.lower()}:{FND_TID}"
    st, tx = _call(url, headers=headers)
    _save("rarible_item_bad_chain", url, st, tx)
    # pesquisa por nome SEM filtro de cadeia (unicidade, vista do caçador):
    # fixa items[].meta.name, que a captura de 25/08 não fixou
    url = "https://api.rarible.org/v0.1/items/search"
    st, tx = _call(url, body={"size": 50, "filter": {"fullText": {"text": "Uncle Pump"}}},
                   headers=headers)
    _save("rarible_search_named", url, st, tx, query="Uncle Pump")


def cap_opensea() -> None:
    """Mede se a OpenSea pode fazer o que a Rarible faz no jogo-alvo (10/09:
    a Rarible só tem Free = 100 pedidos/MÊS e Enterprise por contacto):
      · pesquisa por texto FILTRADA por cadeia (search guard: o alvo tem de
        aparecer ao pesquisar o próprio nome; a pista não o pode trazer);
      · pesquisa SEM filtro de cadeia (unicidade do nome);
      · GET do item por cadeia, incluindo a forma do 404 (sonda de cadeia).
    O que o parser precisa de encontrar na resposta da pesquisa: contrato,
    identifier e cadeia de cada NFT. Cabeçalhos x-ratelimit-* ficam no _meta
    (a quota por janela decide se chega para uma hunt)."""
    key = _env("OPENSEA_API_KEY")
    if not key:
        print("  OPENSEA_API_KEY em falta — salto a secção opensea")
        return
    headers = {"X-API-KEY": key}
    base = "https://api.opensea.io/api/v2"
    name = "Ancient Future"               # FND #1 (token vivo da captura rpc)
    q = urllib.parse.quote(name)
    for tag, url in (
        ("opensea_search_ethereum",
         f"{base}/search?query={q}&chains=ethereum&asset_types=nft&limit=50"),
        ("opensea_search_all",
         f"{base}/search?query={q}&asset_types=nft&limit=50"),
        # uma frase de pista, filtrada: a forma de "nada relevante"
        ("opensea_search_clue",
         f"{base}/search?query={urllib.parse.quote('where calendars run out of pages')}"
         f"&chains=ethereum&asset_types=nft&limit=50"),
    ):
        st, tx, rate = _call_h(url, headers=headers)
        _save(tag, url, st, tx, query=name, rate=rate)
    for ch in ("ethereum", "base", "matic", "arbitrum", "optimism", "zora"):
        url = f"{base}/chain/{ch}/contract/{FND.lower()}/nfts/1"
        st, tx, rate = _call_h(url, headers=headers)
        _save(f"opensea_item_{ch}", url, st, tx, chain=ch, rate=rate)
    url = f"{base}/chain/nope/contract/{FND.lower()}/nfts/1"
    st, tx, rate = _call_h(url, headers=headers)
    _save("opensea_item_bad_chain", url, st, tx, rate=rate)


def cap_pages(name: str, urls: list[str]) -> None:
    if not urls:
        print(f"  {name}: sem URLs — passa-os como argumentos "
              f"(python scripts/capturar_target.py {name} <url> [<url>])")
        return
    for i, u in enumerate(urls):
        st, tx = _call(u, timeout=40)
        _save(f"{name}_page_{i}", u, st, tx, html_bytes=len(tx),
              has_next_data="__NEXT_DATA__" in tx,
              has_contract=FND.lower() in tx.lower())


def main(argv: list[str]) -> int:
    sections = {"rpc": cap_rpc, "gateways": cap_gateways, "rarible": cap_rarible,
                "opensea": cap_opensea}
    wanted = [a for a in argv if a in sections or a in ("foundation", "superrare")]
    urls = [a for a in argv if a.startswith("http")]
    if not wanted:
        wanted = ["foundation", "superrare", "rarible", "opensea", "rpc", "gateways"]
    print(f"capturas → {OUT}")
    for w in wanted:
        print(f"[{w}]")
        try:
            if w == "foundation":
                mine = [u for u in urls if "foundation.app" in u]
                cap_pages("foundation", mine or FOUNDATION_URLS)
            elif w == "superrare":
                mine = [u for u in urls if "superrare.com" in u]
                cap_pages("superrare", mine or SUPERRARE_URLS)
            else:
                sections[w]()
        except Exception as e:  # noqa: BLE001 — uma secção nunca mata as outras
            print(f"  FALHOU: {type(e).__name__}: {e}")
    print("feito. Verifica que nenhum ficheiro contém chaves antes de commitar:")
    print(f"  grep -ril 'alch_\\|api-key\\|apikey' {OUT} || echo limpo")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
