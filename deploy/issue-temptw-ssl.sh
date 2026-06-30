#!/usr/bin/env bash
# DNS 生效后执行：为 temptw.cosmicps.com 启用 HTTPS（证书已存在则跳过签发）
set -euo pipefail

DOMAIN="temptw.cosmicps.com"
WEBROOT="/www/wwwroot/tianwang"
NGINX_FULL="/www/server/panel/vhost/nginx/tianwang-temptw.conf"
HTTPS_CONF="/root/tianwang-temptw-https.conf"
CERT_DIR="/root/.acme.sh/${DOMAIN}_ecc"
FULLCHAIN="${CERT_DIR}/fullchain.cer"
PRIVKEY="${CERT_DIR}/${DOMAIN}.key"

echo ">>> 检查 DNS..."
IP=$(dig +short "$DOMAIN" @119.29.29.29 | tail -1)
if [ -z "$IP" ]; then
  echo "DNS 尚未生效：$DOMAIN 无法解析"
  echo "请确认已添加 A 记录 temptw.cosmicps.com -> 123.207.10.114 并等待 5-10 分钟"
  exit 1
fi
echo "DNS 已解析: $DOMAIN -> $IP"

if [ -f "$FULLCHAIN" ] && [ -f "$PRIVKEY" ]; then
  echo ">>> 已有有效证书，跳过签发"
else
  echo ">>> 申请 Let's Encrypt 证书..."
  /root/.acme.sh/acme.sh --set-default-ca --server letsencrypt
  /root/.acme.sh/acme.sh --issue -d "$DOMAIN" -w "$WEBROOT" --server letsencrypt
fi

echo ">>> 启用 HTTPS nginx 配置..."
if [ -f "$HTTPS_CONF" ]; then
  cp "$HTTPS_CONF" "$NGINX_FULL"
else
  echo "缺少 $HTTPS_CONF，请先运行 ./deploy.sh"
  exit 1
fi

nginx -t && nginx -s reload
echo ">>> HTTPS 已启用: https://$DOMAIN"
