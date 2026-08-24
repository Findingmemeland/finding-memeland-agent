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
/// IMPORTANT: `name_` and `description_` MUST be JSON-escaped by the deployer
/// (Web3Minter.json_escape) — they are embedded verbatim into the metadata JSON.
/// The single token (#1) is minted to the deploying wallet in the constructor,
/// so one transaction deploys AND mints. The wallet is a fresh, non-linkable
/// relic wallet; ownership transfers to the winner at reveal (package 4).
contract RelicNFT is ERC721 {
    string private _description;   // already JSON-escaped; includes "code: XXXXXXXX"
    string private _imageURI;      // pinned IPFS (ipfs://... or a gateway URL)
    string private _artist;        // already JSON-escaped; VARIED per relic (no shared artist)

    // symbol_ and artist_ are generated fresh per relic (no shared literal), so
    // an observer cannot filter the whole pool by symbol or by artist.
    constructor(
        string memory name_,
        string memory symbol_,
        string memory description_,
        string memory imageURI_,
        string memory artist_
    ) ERC721(name_, symbol_) {
        _description = description_;
        _imageURI = imageURI_;
        _artist = artist_;
        _safeMint(msg.sender, 1);
    }

    function tokenURI(uint256 tokenId) public view override returns (string memory) {
        require(tokenId == 1, "RelicNFT: nonexistent token");
        bytes memory json = abi.encodePacked(
            '{"name":"', name(),
            '","description":"', _description,
            '","image":"', _imageURI,
            '","attributes":[{"trait_type":"artist","value":"', _artist, '"}]}'
        );
        return string(
            abi.encodePacked("data:application/json;base64,", Base64.encode(json))
        );
    }
}
