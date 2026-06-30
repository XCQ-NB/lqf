// 水印照片右上角「扫码导航」二维码（依赖 qrcode.min.js + mobile.html 中的 wgs84ToGcj02）

function buildNavUrl(wgsLat, wgsLng, name) {
  var gcj = typeof wgs84ToGcj02 === 'function'
    ? wgs84ToGcj02(wgsLng, wgsLat)
    : { lng: wgsLng, lat: wgsLat };
  var title = encodeURIComponent(name || '拍摄位置');
  return 'https://uri.amap.com/marker?position=' + gcj.lng.toFixed(6) + ',' + gcj.lat.toFixed(6) + '&name=' + title + '&callnative=1';
}

function navRoundRect(ctx, x, y, w, h, r) {
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function drawNavQrWatermark(ctx, canvasW, canvasH, wgsLat, wgsLng, name, compact) {
  if (!wgsLat || !wgsLng || typeof qrcode === 'undefined') return;

  var url = buildNavUrl(wgsLat, wgsLng, name);
  var qr = qrcode(0, 'M');
  qr.addData(url);
  qr.make();

  var modules = qr.getModuleCount();
  var scale = compact ? 0.11 : 0.15;
  var qrSize = Math.round(Math.min(canvasW, canvasH) * scale);
  var pad = Math.round(canvasW * 0.025);
  var boxPad = Math.round(qrSize * 0.1);
  var labelH = compact ? Math.round(qrSize * 0.18) : Math.round(qrSize * 0.24);
  var boxW = qrSize + boxPad * 2;
  var boxH = qrSize + boxPad * 2 + labelH;
  var boxX = canvasW - boxW - pad;
  var boxY = pad;

  ctx.save();
  ctx.shadowColor = 'rgba(0,0,0,0.25)';
  ctx.shadowBlur = 6;
  ctx.shadowOffsetY = 2;
  ctx.fillStyle = 'rgba(255,255,255,0.94)';
  navRoundRect(ctx, boxX, boxY, boxW, boxH, Math.round(boxW * 0.08));
  ctx.fill();
  ctx.shadowBlur = 0;
  ctx.shadowOffsetY = 0;

  var cell = qrSize / modules;
  var qrX = boxX + boxPad;
  var qrY = boxY + boxPad;
  ctx.fillStyle = '#111';
  for (var r = 0; r < modules; r++) {
    for (var c = 0; c < modules; c++) {
      if (qr.isDark(r, c)) {
        ctx.fillRect(qrX + c * cell, qrY + r * cell, cell + 0.6, cell + 0.6);
      }
    }
  }

  var fontSize = Math.max(10, Math.round(labelH * 0.62));
  ctx.font = '600 ' + fontSize + 'px -apple-system, "Microsoft YaHei", sans-serif';
  ctx.fillStyle = '#1565c0';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillText('扫码导航', boxX + boxW / 2, boxY + boxPad + qrSize + labelH / 2);
  ctx.textAlign = 'left';
  ctx.restore();
}
