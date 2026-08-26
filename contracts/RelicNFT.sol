// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// Compile with OpenZeppelin available (npm @openzeppelin/contracts). Fable
// compiles this and passes the ABI + bytecode to Web3Minter (relic_mint.py).
import "@openzeppelin/contracts/token/ERC721/ERC721.sol";
import "@openzeppelin/contracts/utils/Base64.sol";

/// @title RelicNFT — one disposable 1/1 per relic (contract-per-relic).
/// @notice The contract NAME is the relic's name, so marketplace/explorer NAME
/// search surfaces it (probe 2026-08-21: indexes instantly on mint). Metadata is
/// fully ON-CHAIN and immutable — the name and description (which carries the
/// claim code) live in the tokenURI data: URI, so nothing can vanish mid-hunt
/// (decision §9); the image is a PINNED IPFS URI.
///
/// IMPORTANT: `name_`, `description_` and `attributes_` MUST be JSON-escaped /
/// JSON-valid by the deployer (Web3Minter.json_escape) — they are embedded
/// verbatim into the metadata JSON. The single token (#1) is minted to the
/// deploying wallet in the constructor, so one transaction deploys AND mints.
/// The wallet is a fresh, non-linkable relic wallet; ownership transfers to the
/// winner at reveal (package 4).
///
/// ANTI-FINGERPRINT (audit 2026-08-26, P0-1). Every relic used to deploy from
/// this source with constructor arguments only — and constructor arguments do
/// NOT enter the runtime bytecode. So all relic contracts were byte-for-byte
/// identical, and one indexer query on that code listed the entire pool; from
/// there `tokenURI(1)` hands over the claim code without solving a single clue.
/// Three shared signatures had to die, and the fixes live in three places:
///   1. the code itself      -> `provenanceHash` below (unique runtime bytecode)
///   2. the attributes shape -> `_attributes` is now supplied whole, and varies
///   3. the "code: " prefix  -> varied in relic_mint.compose_onchain_description
contract RelicNFT is ERC721 {
    /// Per-relic entropy, deliberately never read by this contract.
    ///
    /// An `immutable` is inlined into the RUNTIME bytecode (unlike a constructor
    /// argument, which is only appended to the deployment payload), so a random
    /// seed here gives every relic a distinct code hash. `public` is not
    /// decoration: the generated getter is what guarantees the optimizer cannot
    /// discard the value, and a provenance hash is an unremarkable thing for an
    /// NFT to expose.
    bytes32 public immutable provenanceHash;

    string private _description;   // already JSON-escaped; carries the claim code
    string private _imageURI;      // pinned IPFS (ipfs://... or a gateway URL)
    string private _attributes;    // full JSON array; shape and trait names VARY per relic

    // symbol_, attributes_ and provenanceHash_ are generated fresh per relic (no
    // shared literal), so an observer cannot filter the pool by symbol, by trait
    // shape, or by contract code.
    constructor(
        string memory name_,
        string memory symbol_,
        string memory description_,
        string memory imageURI_,
        string memory attributes_,
        bytes32 provenanceHash_
    ) ERC721(name_, symbol_) {
        _description = description_;
        _imageURI = imageURI_;
        _attributes = attributes_;
        provenanceHash = provenanceHash_;
        _safeMint(msg.sender, 1);
    }

    function tokenURI(uint256 tokenId) public view override returns (string memory) {
        require(tokenId == 1, "RelicNFT: nonexistent token");
        bytes memory json = abi.encodePacked(
            '{"name":"', name(),
            '","description":"', _description,
            '","image":"', _imageURI,
            '","attributes":', _attributes, '}'
        );
        return string(
            abi.encodePacked("data:application/json;base64,", Base64.encode(json))
        );
    }
}
