#!/usr/bin/env bash
# Make a NEW OpenDuo archive signing key (docs/UPDATES.md "Signing"):
#
#   packaging/signing/new-key.sh DIR
#
# DIR (made 0700; never in git: out/ is ignored) gets
#   gnupg/                   the keyring: an ed25519 certification key (no
#                            expiry) and an ed25519 signing subkey (3 years)
#   primary-secret.asc       the whole secret key: keep it offline; it makes new
#                            subkeys and revokes
#   ci-signing-subkey.asc    the signing subkey only (the primary's secret part
#                            is a stub): the CI secret OPENDUO_SIGNING_KEY
#   revocation.asc           the revocation certificate
#   FINGERPRINT              the primary key's fingerprint
# and the public key goes to packaging/keyring/openduo-archive-keyring.asc (in
# git, into the openduo-archive-keyring package). No passphrase: the
# directory's mode protects it on this machine; the owner decides where the
# key lives for good (a passphrase: gpg --homedir DIR/gnupg --passwd FPR).
set -euo pipefail
R=$(cd "$(dirname "$0")/../.." && pwd)
DIR=${1:?usage: new-key.sh DIR}
[ ! -e "$DIR/gnupg" ] || { echo "$DIR/gnupg exists: a key is there already" >&2; exit 1; }
umask 077
mkdir -p "$DIR/gnupg"
chmod 0700 "$DIR" "$DIR/gnupg"
D=$(cd "$DIR" && pwd)
image=openduo-pkg:trixie
docker image inspect "$image" >/dev/null 2>&1 || docker build -q -t "$image" "$R/packaging" >/dev/null
# PUBLIC_DIR: where the public key goes (the package test's throwaway key:
# not over the real one)
PUB=${PUBLIC_DIR:-$R/packaging/keyring}
mkdir -p "$PUB"
docker run --rm -u "$(id -u):$(id -g)" -e GNUPGHOME=/key/gnupg -e KEY_NAME="${KEY_NAME:-}" -v "$D":/key -v "$(cd "$PUB" && pwd)":/pub \
    "$image" bash -euc '
umask 077
uid="${KEY_NAME:-OpenDuo Archive Signing Key}"
gpg --batch --quiet --passphrase "" --quick-generate-key "$uid" ed25519 cert never
fpr=$(gpg --batch --with-colons --list-keys "$uid" | awk -F: "/^fpr:/ {print \$10; exit}")
gpg --batch --quiet --passphrase "" --quick-add-key "$fpr" ed25519 sign 3y
gpg --batch --armor --export-secret-keys "$fpr" > /key/primary-secret.asc
gpg --batch --armor --export-secret-subkeys "$fpr" > /key/ci-signing-subkey.asc
cp "/key/gnupg/openpgp-revocs.d/$fpr.rev" /key/revocation.asc
echo "$fpr" > /key/FINGERPRINT
umask 022
gpg --batch --armor --export "$fpr" > /pub/openduo-archive-keyring.asc
gpg --batch --list-keys --keyid-format long "$fpr"
'
chmod 0600 "$D"/*.asc "$D/FINGERPRINT"
echo "key in $D (0700); public key: packaging/keyring/openduo-archive-keyring.asc"
