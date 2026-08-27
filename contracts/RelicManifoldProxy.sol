// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

/// @title RelicManifoldProxy — deploys a relic as a Manifold `ERC721Creator` proxy.
///
/// WHY (probe 2026-08-26, Probe_Manifold_Proxy.md): every relic used to be its own
/// `RelicNFT.sol` deployment, and a pool compiled from one source is one bytecode
/// class — enumerable with a single indexer query. A Manifold creator contract is
/// a 298-byte EIP-1967 proxy whose RUNTIME is byte-for-byte identical across the
/// thousands of collections Manifold Studio has deployed on Base; the collection
/// name, symbol and tokens live in the shared implementation's storage, not in
/// the code. So a relic deployed as that exact proxy is indistinguishable from
/// the crowd by `eth_getCode`.
///
/// HOW: this contract is CONSTRUCTOR-ONLY. It writes the implementation into the
/// EIP-1967 slot, delegatecalls `initialize(name, symbol)` on the implementation
/// (which makes `msg.sender` — the relic wallet — the owner), and then RETURNS
/// the Manifold proxy runtime VERBATIM (`runtime_`, the bytes read from an
/// existing Manifold proxy on the same chain) as the deployed code. Nothing of
/// this constructor survives on-chain; only those 298 bytes do.
///
/// The minter then calls `mintBase(wallet, uri)` and `renounceOwnership()` on
/// the new proxy — the second is mandatory: Manifold's `setTokenURI` is
/// owner/admin-only, so renouncing is what makes the metadata immutable.
contract RelicManifoldProxy {
    // keccak256("eip1967.proxy.implementation") - 1
    bytes32 private constant _IMPLEMENTATION_SLOT =
        0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc;

    constructor(
        address implementation_,
        string memory name_,
        string memory symbol_,
        bytes memory runtime_
    ) {
        require(implementation_.code.length > 0, "impl has no code");
        require(runtime_.length > 0, "empty runtime");
        assembly {
            sstore(_IMPLEMENTATION_SLOT, implementation_)
        }
        (bool ok, bytes memory ret) = implementation_.delegatecall(
            abi.encodeWithSignature("initialize(string,string)", name_, symbol_)
        );
        if (!ok) {
            assembly {
                revert(add(ret, 0x20), mload(ret))
            }
        }
        assembly {
            return(add(runtime_, 0x20), mload(runtime_))
        }
    }
}
