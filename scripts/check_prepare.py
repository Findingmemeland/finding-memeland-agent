#!/usr/bin/env python3
"""check_prepare.py — the live test of the larder, against the real network.

THE RULE THIS SCRIPT EXISTS FOR (Hunt #11 post-mortem, 16/09): nothing is
"ready" until it has run against the network. A green suite and a GREEN gate
said ready on 14/09; the image path had never once touched a gateway, and it
died in front of an audience.

It runs the PRODUCTION code — `TargetFinder` — not a copy of it. A script
that reimplements the logic is a script that can pass while production
fails, which is the same mistake one level up.

  1. the two questions each source must answer: totalSupply() and
     tokenByIndex(0). This is the measurement that decides the source list.
  2. real draws through TargetFinder.fill(), with the cause of every
     rejection and the wall time.

Nothing is written, nothing is published, no LLM is called.

    python scripts/check_prepare.py               # sources + 10 draws
    python scripts/check_prepare.py --want 3      # stop after 3 finds
    python scripts/check_prepare.py --draws 40    # a real rate measurement
"""
from __future__ import annotations

import argparse
import sys
import time
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, "src")

from finding_memeland.config import get_settings                     # noqa: E402
from finding_memeland.main import _http_get, _http_post              # noqa: E402
from finding_memeland.target.adapters import (                       # noqa: E402
    Erc721Metadata, abi_uint, chain_rpcs, gateway_url, sniff_media_type,
)
from finding_memeland.target.prepare import (                        # noqa: E402
    SOURCES, Larder, Source, TargetFinder, enumerable_sources,
)
from finding_memeland.target.sources import (                        # noqa: E402
    SEL_TOKENBYINDEX, SEL_TOTAL, ChainEoaCheck,
)

# measured here even though they are not in SOURCES — the point is to decide
# whether they come back in
CANDIDATE_SOURCES = SOURCES + (
    Source("makersplace", "ethereum", "0x2963ba471e265e5f51cafafca78310fe87f8e6d1"),
    Source("superrare1", "ethereum", "0x41a322b28d0ff354040e2cbc676f0320d8c8850d"),
)

OK, NO = "✓", "✗"
PROBE_BYTES = 4096


def make_probe(gateway: str, timeout: float):
    """A RANGED read: the first few KB plus the total size. Proves the bytes
    are there (Hunt #11) without pulling a 15 MB artwork — which is what
    timed out on the 16/09 run.

    8 s, not 20 (measured 17/09): of 282 s spent on 20 draws, ~100 went to
    gateways that were never going to answer. A ranged 4 KB read that has
    not arrived in 8 s is not arriving, and a dead pin costs the same
    verdict either way."""
    def probe(uri: str):
        url = gateway_url(uri, gateway) or uri
        req = urllib.request.Request(url, headers={
            "User-Agent": "fml-prepare-probe/1.0",
            "Range": f"bytes=0-{PROBE_BYTES - 1}"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            head = r.read(PROBE_BYTES)
            cr = r.headers.get("Content-Range") or ""
            size = int(cr.rsplit("/", 1)[-1]) if "/" in cr else int(
                r.headers.get("Content-Length") or 0)
        if not head or not sniff_media_type(head):
            return None
        return head, size
    return probe


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--draws", type=int, default=10)
    ap.add_argument("--want", type=int, default=99)
    ap.add_argument("--timeout", type=float, default=8.0)
    args = ap.parse_args()

    s = get_settings()
    rpcs = chain_rpcs({"ethereum": s.eth_rpc_url}, http_post=_http_post)
    if "ethereum" not in rpcs:
        print("ETH_RPC_URL is empty — nothing to measure")
        return 2
    rpc = rpcs["ethereum"]
    gateway = s.target_ipfs_gateway
    if not gateway.lower().startswith("http"):
        print(f"{NO} TARGET_IPFS_GATEWAY has no scheme: {gateway!r}")
        return 2

    def total_supply(chain, contract):
        return int(rpc.eth_call(contract, SEL_TOTAL), 16)

    def token_by_index(chain, contract, idx):
        return int(rpc.eth_call(contract, SEL_TOKENBYINDEX + abi_uint(idx)), 16)

    print("=" * 68)
    print("SOURCES — totalSupply() and tokenByIndex(0)")
    print("=" * 68)
    ok, dropped = enumerable_sources(CANDIDATE_SOURCES, total_supply=total_supply,
                                     token_by_index=token_by_index)
    total = 0
    for src in ok:
        n = total_supply(src.chain, src.contract)
        total += n
        print(f"  {OK} {src.slug:12} enumera — totalSupply {n:,}")
    for slug, why in dropped.items():
        print(f"  {NO} {slug:12} FORA — {why}")
    if not ok:
        print("\nno usable source — stop here")
        return 1
    print(f"\nuniverso das fontes utilizáveis: {total:,} peças")

    print("\n" + "=" * 68)
    print(f"DRAWS — até {args.draws} sorteios ou {args.want} encontrados")
    print("=" * 68)
    finder = TargetFinder(
        sources=ok, total_supply=total_supply, token_by_index=token_by_index,
        read_token=Erc721Metadata(rpcs=rpcs, gateway=gateway,
                                  http_get=_http_get).read,
        probe_image=make_probe(gateway, args.timeout),
        owner_is_eoa=ChainEoaCheck(rpcs=rpcs),
        # uniqueness costs marketplace quota: OFF here unless a key is set
        name_is_unique=(lambda *a: True),
        now_iso=lambda: datetime.now(timezone.utc).isoformat())

    larder = Larder()
    t0 = time.time()
    tally = finder.fill(larder, want=args.want, max_draws=args.draws,
                        notify=lambda t: print("  " + t), every=1)
    dt = time.time() - t0

    print("\n" + "=" * 68)
    print(f"{tally.render()}")
    print(f"{dt:.0f}s  ·  {dt / max(tally.draws, 1):.1f}s por sorteio")
    if tally.draws:
        print(f"taxa de sobrevivência: {100 * tally.found / tally.draws:.0f}%"
              "   (unicidade não testada aqui — gasta quota)")
    print("=" * 68)
    return 0 if tally.found else 1


if __name__ == "__main__":
    raise SystemExit(main())
