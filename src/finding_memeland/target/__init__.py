"""Option A target machinery: selecting an EXISTING third-party NFT as the
hunt's hidden target, instead of minting a relic.

Redesign after the Hunt #10 harvest exploit (post-mortem 2026-09-02, Opus
review 2026-09-04): a target that carries no secret cannot be harvested. The
only residual attack the target universe mitigates is multiple-choice
guessing, which these modules are built to keep unwinnable.

THE RULES THIS PACKAGE CLAIMS TO OBEY — every review verifies each one
against the code (grep + execution path), never against the comments. All
of them were born from a real bug; the family they share is "systems that
confuse 'I saw nothing' with 'there is nothing'".

  R1  No component that talks to a chain has a chain by default. The chain
      travels WITH the entry (PlatformItem → SnapshotEntry → Target.id()).
      Marketplaces never imply a chain; only explorers do.           (sources, claim)
  R2  A guard whose approval is "found nothing" first proves it can see
      what it looks for (a canary): ClueSearchGuard, MarketNameUniqueness,
      ChainContractLister, ChainEoaCheck, EraDiscovery (exact mint count).
  R3  The decoy rule: what stays constant across repeated calls is what
      gets identified. Sealed batch where the target is fixed (live check,
      image); fresh decoys where the candidate varies (judge, uniqueness);
      rotation of providers PER BATCH, no failover inside a batch; a ghost
      batch after the accept.                                        (hunt, adapters)
  R4  One token per reply: a claim naming >1 token is malformed — never a
      match, no guess spent, format reply.                           (claim, matcher)
  R5  Detect degradation, not only death: the hold has two ceilings —
      episode (6h) AND accumulated per hunt (12h); past either the operator
      is CALLED to decide, nothing is decided automatically.         (integration)
  R6  Global name uniqueness is lazy: checked at draw time inside a decoy
      batch, never in the refresh; the gate multiplies a sampled rate.
  R7  The gateway only appears where the read does NOT repeat (image batch,
      refresh). The repeated path is RPC only: tokenURI → content id (CID +
      path, transport stripped), ownerOf → burn.                     (hunt, adapters)
  R8  Never publish a cause that was not measured (Opus audit, 09/09). Any
      check that feeds a public post is TRI-STATE: measured-yes /
      measured-no / WE-COULD-NOT-MEASURE — and the third is printed as our
      failure, never as a fact about the token or its owner. (LiveHash,
      templates._CAUSE_LINE, _live_hash_phrase)
  R9  The treasure is somebody's work (Opus, 09/09). The reveal CREDITS the
      author — title, artist from the token's own metadata, link to the
      piece — and the attached image carries alt-text with both. A takedown
      request from the artist is honoured THE SAME DAY, no discussion:
      delete the reveal's media, keep the text and the link. Written before
      it is needed, not after.             (selector.artist_of, templates, integration)
"""
