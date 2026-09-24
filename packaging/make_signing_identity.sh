#!/bin/bash
# 一次性生成本机开发签名身份（自签名证书，10 年有效）。
# 作用：ad-hoc 签名每次构建的身份都不同，macOS 会把每个新构建当成全新 App，
# 导致 App Management / 本地网络等隐私授权反复重弹；改用固定证书签名后，
# 同一证书签出的所有构建共享同一「指定要求(designated requirement)」，
# 授权一次即可跨版本保留。
# 产物只写入登录钥匙串；本脚本与仓库均不包含任何私钥材料。重复执行安全。
set -euo pipefail

NAME="AirfareMonitor Local Dev"
KEYCHAIN="$HOME/Library/Keychains/login.keychain-db"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

if security find-identity -v -p codesigning 2>/dev/null | grep -q "$NAME"; then
  echo "已存在签名身份：$NAME（跳过生成）"
  exit 0
fi

openssl req -newkey rsa:2048 -nodes -keyout "$WORK/key.pem" \
  -x509 -days 3650 -out "$WORK/cert.pem" \
  -subj "/CN=${NAME}/O=AirfareMonitor" \
  -addext "keyUsage=critical,digitalSignature" \
  -addext "extendedKeyUsage=codeSigning" >/dev/null 2>&1

# -T 预授权 codesign/security 使用该私钥，避免每次签名弹钥匙串确认框。
security import "$WORK/key.pem" -k "$KEYCHAIN" -T /usr/bin/codesign -T /usr/bin/security
security import "$WORK/cert.pem" -k "$KEYCHAIN"
# 自签名证书必须显式信任才会被当作有效签名身份；用户域 trustRoot 即可
# （实测 macOS 27：带 -p 策略参数会报 parameter not valid，无策略的 trustRoot 可静默成功）。
security add-trusted-cert -r trustRoot -k "$KEYCHAIN" "$WORK/cert.pem"
security verify-cert -c "$WORK/cert.pem" >/dev/null
security find-identity -v -p codesigning | grep "$NAME" || true
echo "完成：后续 packaging/build_release_mac.py 将自动使用该身份签名。"
