#!/bin/sh
# Generate a self-signed CA plus one certificate per service (SAN = service DNS name).
# Used at image build time; can also be run on the host: sh certs/generate-certs.sh ./certs
set -eu

OUT="${1:-/certs}"
mkdir -p "$OUT"

gen_ca() {
    openssl genrsa -out "$OUT/ca.key" 2048 >/dev/null 2>&1
    openssl req -x509 -new -key "$OUT/ca.key" -sha256 -days 825 \
        -subj "/CN=monster-tamers-ca" -out "$OUT/ca.crt"
}

gen_cert() {
    name="$1"
    openssl genrsa -out "$OUT/$name.key" 2048 >/dev/null 2>&1
    openssl req -new -key "$OUT/$name.key" -subj "/CN=$name" -out "$OUT/$name.csr"
    cat > "$OUT/$name.ext" <<EXT
subjectAltName=DNS:$name,DNS:localhost,IP:127.0.0.1
extendedKeyUsage=serverAuth
keyUsage=digitalSignature,keyEncipherment
EXT
    openssl x509 -req -in "$OUT/$name.csr" -CA "$OUT/ca.crt" -CAkey "$OUT/ca.key" \
        -CAcreateserial -days 825 -sha256 -extfile "$OUT/$name.ext" -out "$OUT/$name.crt"
    rm -f "$OUT/$name.csr" "$OUT/$name.ext"
}

gen_ca
for name in gateway auth players monsters teams encounters history db-manager localhost; do
    gen_cert "$name"
done
rm -f "$OUT/ca.srl"
