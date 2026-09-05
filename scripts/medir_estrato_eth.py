#!/usr/bin/env python3
"""Teste rápido: dimensão dos estratos de arte 1/1 em Ethereum mainnet.
Contratos-farol conhecidos (SuperRare, Foundation, KnownOrigin): totalSupply
exacto + amostra de nomes via tokenURI. Corre do raiz do repo:

    .venv/bin/python scripts/medir_estrato_eth.py
"""
import json
import random
import re
import ssl
import urllib.request

RPCS = ["https://ethereum-rpc.publicnode.com", "https://eth.drpc.org",
        "https://cloudflare-eth.com", "https://eth.llamarpc.com"]
try:
    import certifi
    CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    CTX = ssl.create_default_context()
UA = {"User-Agent": "fml-probe/1.0", "Content-Type": "application/json"}
SEL_TOTAL = "0x18160ddd"
SEL_TOKENURI = "0xc87b56dd"
SEL_TOKENBYINDEX = "0x4f6ccce7"
WORD = re.compile(r"[A-Za-zÀ-ÿ]{2,}")
SERIAL = re.compile(r"[\s\-–—_.:]*(?:#|n[oº]\.?\s*)?\d{1,10}\s*$", re.I)

CONTRACTS = [
    ("SuperRare V2", "0xb932a70a57673d89f4acffbe830e8ed7f75fb9e0"),
    ("SuperRare V1", "0x41a322b28d0ff354040e2cbc676f0320d8c8850d"),
    ("Foundation",   "0x3b3ee1931dc30c1957379fac9aba94d1c48a5405"),
    ("KnownOrigin V2", "0xfbeef911dc5821886e1dda71586d90ed28174b7d"),
]


def rpc_call(url, method, params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                       "params": params}).encode()
    req = urllib.request.Request(url, data=body, headers=UA)
    with urllib.request.urlopen(req, timeout=20, context=CTX) as r:
        out = json.load(r)
    if "error" in out:
        raise RuntimeError(out["error"].get("message", "err"))
    return out["result"]


def pick_rpc():
    for u in RPCS:
        try:
            rpc_call(u, "eth_blockNumber", [])
            return u
        except Exception as e:
            print(f"  ({u}: {str(e)[:60]})")
    raise SystemExit("sem RPC ETH")


def eth_call(url, to, data):
    return rpc_call(url, "eth_call", [{"to": to, "data": data}, "latest"])


def decode_string(hexdata):
    if not hexdata or hexdata == "0x":
        return None
    b = bytes.fromhex(hexdata[2:])
    if len(b) < 64:
        return None
    ln = int.from_bytes(b[32:64], "big")
    return b[64:64 + ln].decode("utf-8", "replace")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "fml-probe/1.0"})
    with urllib.request.urlopen(req, timeout=20, context=CTX) as r:
        return r.read(200_000)


def resolve_meta(uri):
    if uri.startswith("data:"):
        import base64
        payload = uri.split(",", 1)[1]
        raw = base64.b64decode(payload) if ";base64" in uri else payload.encode()
        return json.loads(raw)
    if uri.startswith("ipfs://"):
        path = uri[7:]
        uri = "https://ipfs.io/ipfs/" + (path[5:] if path.startswith("ipfs/") else path)
    if uri.startswith("http"):
        return json.loads(fetch(uri))
    return None


def base_name(name):
    b = name.strip()
    while True:
        s = SERIAL.sub("", b).strip()
        if s == b or not s:
            break
        b = s
    return b or name.strip()


def main():
    url = pick_rpc()
    print(f"RPC ETH: {url}\n")
    rng = random.Random(7)
    grand = 0
    for label, contract in CONTRACTS:
        try:
            supply = int(eth_call(url, contract, SEL_TOTAL), 16)
        except Exception as e:
            print(f"{label}: totalSupply falhou ({str(e)[:60]})")
            continue
        print(f"{label}: totalSupply = {supply:,}")
        grand += supply
        ok = tried = 0
        names = []
        attempts = 0
        while tried < 8 and attempts < 24:
            attempts += 1
            # tokenIds nem sempre são densos: tenta tokenByIndex, senão id directo
            idx = rng.randrange(supply)
            try:
                tid = int(eth_call(url, contract,
                                   SEL_TOKENBYINDEX + idx.to_bytes(32, "big").hex()), 16)
            except Exception:
                tid = idx + 1
            try:
                uri = decode_string(eth_call(url, contract,
                                             SEL_TOKENURI + tid.to_bytes(32, "big").hex()))
                meta = resolve_meta(uri) if uri else None
            except Exception:
                continue
            if not isinstance(meta, dict) or not meta.get("name"):
                continue
            tried += 1
            b = base_name(str(meta["name"]))
            good = len(WORD.findall(b)) >= 2
            ok += good
            names.append(("✅" if good else "—") + " " + b[:46])
        for n in names:
            print("   " + n)
        if tried:
            print(f"   nomes-base 2+ palavras: {ok}/{tried}\n")
        else:
            print("   (amostra falhou — metadata inacessível daqui)\n")
    print(f"TOTAL só nestes contratos: {grand:,} tokens 1/1")


if __name__ == "__main__":
    main()
