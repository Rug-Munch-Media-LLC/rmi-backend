# Keystore exposure — rotation REQUIRED

**Status: `rotation: REQUIRED, owner action`**
**Severity: CRITICAL**
**Date opened: 2026-10-23**

## What happened

`data/wallets/keystore_v2.enc` was **tracked in git** and therefore present in every
clone and every mirror of `rmi-backend`. The repository is public, so the file has been
publicly retrievable.

| fact | value |
|---|---|
| path | `data/wallets/keystore_v2.enc` |
| size | 30,949 bytes, 357 lines |
| contents | **71 encrypted mnemonic records** — every value carries the `ENC_V2` prefix |
| format | labelled, quoted key → `ENC_V2…` value pairs; no plaintext mnemonic present |
| code references | none found — nothing in the tree reads this file, so it is dead weight as well as an exposure |
| commit at time of audit | `1a85f68` (`main`) |
| git blob | `1b06cf7b680d558a25fdc8900e312988d39771d9` |

Exposure confirmed 2026-10-23 on all three mirrors:
GitHub `Rug-Munch-Media-LLC/rmi-backend` (public), Codeberg `RugMunchMedia/rmi-backend`
(public), GitLab `rug-munch-media-group/rmi-backend` (public, HTTP 200 unauthenticated).

**Nothing was decrypted during this audit.** The records are `ENC_V2` ciphertext; whether
they are recoverable depends entirely on whether the `ENC_V2` key was also exposed. Treat
them as recoverable and therefore as compromised.

## Index remediation applied (staged, NOT committed)

- `git rm --cached data/wallets/keystore_v2.enc` — removed from the index, file kept on disk
- `.gitignore` += `data/wallets/*.enc`
- working-tree file untouched (still 30,949 bytes, 71 records)

This stops *future* commits from carrying the file. It does not undo the past.

## History: the blob remains, and that is why rotation is mandatory

The blob `1b06cf7b…` is still reachable in the history of `main` and in every existing
clone and mirror. **No history rewrite was performed** — rewriting a public mirror does not
recall the copies that were already fetched, and a force-push would invalidate unrelated
work. Rotation and removal of the exposure are the mitigation, not history surgery. If the
operator later decides a rewrite is required for compliance, it must be paired with the
rotation below regardless.

## Rotation procedure (owner action)

> Order matters. Rotate first, clean the repo second, verify last.

1. **Assume all 71 mnemonics are compromised.** This is the default until proven otherwise;
   proving otherwise requires the `ENC_V2` key to have never left a trusted host, which is
   not demonstrable for a file that sat in a public repository.
2. **Inventory.** Decrypt the keystore *on an offline host* and enumerate the derived
   addresses. Do not decrypt on a shared or public host, and do not write plaintext to disk.
   Record only addresses in the incident note.
3. **Sweep the funds.** For every address with a balance or allowance, move assets to a
   freshly generated wallet. Revoke token approvals (`approve(0)`) on every chain the
   addresses touched. Check for pending delegations, staking positions and NFT listings.
4. **Generate a new keystore.** New mnemonics, generated on an offline/hardware source.
   Never reuse a mnemonic that was in the exposed file, even if it never held funds.
5. **Re-encrypt with a new key.** New `ENC_V2` key material, distinct from the old key.
   Store the key in the secret manager (`/srv/secrets/` or the fleet password store) —
   never in the repository, never in `.env`, never in a compose file, never in a shell
   history.
6. **Place the new keystore outside the working tree** (e.g. `/srv/secrets/…` or a
   runtime path), and have the service read it from there. Nothing matching
   `data/wallets/*.enc` may ever be tracked again.
7. **Verify the guard.** `git ls-files | grep -E '\.enc$'` must return nothing.
8. **Record completion** in this document: date, operator, addresses swept, new keystore
   location (path only). Never record mnemonics, passphrases or key material here.

## Never do this

- Never commit a keystore, `ENC_V2` key, or mnemonic — not even briefly, not even to a
  private branch, not even base64-wrapped. History is forever.
- Never paste mnemonics or ciphertext into chat, tickets, PRs, logs or agent prompts.
- Never "fix" this by deleting the file in a later commit and calling it resolved. The
  blob stays retrievable; only rotation closes the exposure.
- Never reuse an exposed mnemonic for a new wallet.

## Owner action checklist

- [ ] Decrypted offline and enumerated all 71 addresses
- [ ] Funds swept to freshly generated wallets
- [ ] Token approvals revoked on every touched chain
- [ ] New keystore generated and encrypted under **new** key material
- [ ] Key stored in the secret manager; keystore stored outside the repo
- [ ] `git ls-files | grep -E '\.enc$'` returns empty
- [ ] This document updated with completion date and operator
