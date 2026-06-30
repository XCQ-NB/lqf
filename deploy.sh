#!/usr/bin/env bash
# 本地开发完成后，一键上传到服务器并重启后端
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=/dev/null
source "$ROOT/deploy/server.env"

SSH_OPTS=(-o StrictHostKeyChecking=no)

echo ">>> 上传 tianwang 代码..."
sshpass -p "$SERVER_PASSWORD" rsync -avz --delete \
  -e "ssh -o StrictHostKeyChecking=no" \
  --exclude '__pycache__' \
  --exclude '*.pyc' \
  --exclude '.DS_Store' \
  --exclude 'backend/data/tianwang.db' \
  "$ROOT/tianwang/" \
  "${SERVER_USER}@${SERVER_HOST}:${REMOTE_APP_DIR}/"

echo ">>> 上传 nginx / systemd 配置..."
sshpass -p "$SERVER_PASSWORD" scp "${SSH_OPTS[@]}" \
  "$ROOT/deploy/nginx/tianwang-local.conf" \
  "${SERVER_USER}@${SERVER_HOST}:/www/server/panel/vhost/nginx/tianwang-local.conf"

sshpass -p "$SERVER_PASSWORD" scp "${SSH_OPTS[@]}" \
  "$ROOT/deploy/nginx/tianwang-ip.conf" \
  "${SERVER_USER}@${SERVER_HOST}:/www/server/panel/vhost/nginx/tianwang-ip.conf"

sshpass -p "$SERVER_PASSWORD" scp "${SSH_OPTS[@]}" \
  "$ROOT/deploy/nginx/tianwang-temptw.conf" \
  "${SERVER_USER}@${SERVER_HOST}:/root/tianwang-temptw-https.conf"

sshpass -p "$SERVER_PASSWORD" scp "${SSH_OPTS[@]}" \
  "$ROOT/deploy/nginx/tianwang-temptw-http-bootstrap.conf" \
  "${SERVER_USER}@${SERVER_HOST}:/root/tianwang-temptw-http-bootstrap.conf"

# 证书已存在 → 保持 HTTPS；否则先用 HTTP bootstrap 便于首次签发证书
sshpass -p "$SERVER_PASSWORD" ssh "${SSH_OPTS[@]}" "${SERVER_USER}@${SERVER_HOST}" \
  'if [ -f /root/.acme.sh/temptw.cosmicps.com_ecc/fullchain.cer ]; then \
     cp /root/tianwang-temptw-https.conf /www/server/panel/vhost/nginx/tianwang-temptw.conf; \
   else \
     cp /root/tianwang-temptw-http-bootstrap.conf /www/server/panel/vhost/nginx/tianwang-temptw.conf; \
   fi'

sshpass -p "$SERVER_PASSWORD" scp "${SSH_OPTS[@]}" \
  "$ROOT/deploy/systemd/tianwang-backend.service" \
  "${SERVER_USER}@${SERVER_HOST}:/etc/systemd/system/tianwang-backend.service"

echo ">>> 重启后端服务..."
sshpass -p "$SERVER_PASSWORD" ssh "${SSH_OPTS[@]}" "${SERVER_USER}@${SERVER_HOST}" \
  "systemctl daemon-reload && systemctl enable tianwang-backend && systemctl restart tianwang-backend && systemctl status tianwang-backend --no-pager | head -15"

echo ">>> 重载 nginx..."
sshpass -p "$SERVER_PASSWORD" ssh "${SSH_OPTS[@]}" "${SERVER_USER}@${SERVER_HOST}" \
  "nginx -t && nginx -s reload"

echo ">>> 尝试为 temptw.cosmicps.com 签发 HTTPS（需 DNS 已生效）..."
sshpass -p "$SERVER_PASSWORD" scp "${SSH_OPTS[@]}" \
  "$ROOT/deploy/issue-temptw-ssl.sh" \
  "${SERVER_USER}@${SERVER_HOST}:/root/issue-temptw-ssl.sh"
sshpass -p "$SERVER_PASSWORD" ssh "${SSH_OPTS[@]}" "${SERVER_USER}@${SERVER_HOST}" \
  "chmod +x /root/issue-temptw-ssl.sh; /root/issue-temptw-ssl.sh" \
  || echo "（HTTPS 证书暂未签发，DNS 生效后请执行: ssh root@${SERVER_HOST} /root/issue-temptw-ssl.sh）"

echo ">>> 部署完成"
