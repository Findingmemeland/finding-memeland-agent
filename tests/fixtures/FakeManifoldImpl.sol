// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;
/// Minimal stand-in for Manifold's ERC721CreatorImplementation, for LOCAL tests of the proxy mechanics only.
contract FakeImpl {
    event Transfer(address indexed from, address indexed to, uint256 indexed tokenId);
    string private _name; string private _symbol; address public owner; uint256 private _next;
    mapping(uint256 => string) private _uri; mapping(uint256 => address) private _owner;
    function initialize(string memory n, string memory s) external { require(bytes(_name).length == 0, "init"); _name = n; _symbol = s; owner = msg.sender; }
    function name() external view returns (string memory) { return _name; }
    function symbol() external view returns (string memory) { return _symbol; }
    modifier adminRequired() { require(msg.sender == owner, "AdminControl: Must be owner or admin"); _; }
    function mintBase(address to, string calldata uri) external adminRequired returns (uint256) { uint256 id = ++_next; _owner[id] = to; _uri[id] = uri; emit Transfer(address(0), to, id); return id; }
    function setTokenURI(uint256 id, string calldata uri) external adminRequired { _uri[id] = uri; }
    function tokenURI(uint256 id) external view returns (string memory) { return _uri[id]; }
    function ownerOf(uint256 id) external view returns (address) { require(_owner[id] != address(0), "ERC721: invalid token ID"); return _owner[id]; }
    function renounceOwnership() external { require(msg.sender == owner, "Ownable: caller is not the owner"); owner = address(0); }
    function safeTransferFrom(address from, address to, uint256 id) external { require(_owner[id] == from && msg.sender == from, "not owner"); _owner[id] = to; emit Transfer(from, to, id); }
}
