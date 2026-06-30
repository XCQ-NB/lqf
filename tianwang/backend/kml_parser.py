"""解析奥维 / 标准 KML：Document 名为站点（如 生米镇相里村），Placemark 为点位。"""
import re
import xml.etree.ElementTree as ET


def _tag(elem):
    return elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag


def _text(elem):
    return (elem.text or "").strip() if elem is not None else ""


def _parse_coordinates(raw):
    if not raw:
        return 0.0, 0.0
    first = raw.strip().split()[0]
    parts = first.split(",")
    if len(parts) < 2:
        return 0.0, 0.0
    try:
        return float(parts[0]), float(parts[1])
    except ValueError:
        return 0.0, 0.0


def _parse_description_meta(description):
    """从奥维 description 提取地址、PON 口、二维码等。"""
    meta = {}
    if not description:
        return meta
    text = description.strip()
    addr = text.split("建设模式")[0].strip()
    if addr and len(addr) > 4:
        meta["address"] = addr
    for key, pattern in (
        ("buildMode", r"建设模式[：:]\s*(\S+)"),
        ("ports", r"总端口[：:]\s*(\d+)"),
        ("qrCode", r"二维码[：:]\s*(\S+)"),
        ("ponPort", r"PON口[：:]\s*(\S+)"),
        ("createDate", r"创建时间[：:]\s*([\d-]+)"),
    ):
        m = re.search(pattern, text)
        if m:
            meta[key] = m.group(1)
    return meta


def parse_kml(content: str):
    """返回 (site_name, placemarks)。"""
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError(f"KML 解析失败: {exc}") from exc

    site_name = ""
    placemarks = []

    for doc in root.iter():
        if _tag(doc) != "Document":
            continue
        for child in doc:
            if _tag(child) == "name" and _text(child):
                site_name = _text(child)
                break
        if site_name:
            break

    for pm in root.iter():
        if _tag(pm) != "Placemark":
            continue
        name = ""
        description = ""
        lng, lat = 0.0, 0.0
        for child in pm:
            t = _tag(child)
            if t == "name":
                name = _text(child)
            elif t == "description":
                description = _text(child)
            elif t == "Point":
                for sub in child.iter():
                    if _tag(sub) == "coordinates":
                        lng, lat = _parse_coordinates(_text(sub))
        if name:
            meta = _parse_description_meta(description)
            placemarks.append(
                {
                    "name": name,
                    "description": description,
                    "longitude": lng,
                    "latitude": lat,
                    "meta": meta,
                }
            )

    if not site_name and placemarks:
        site_name = placemarks[0]["name"].split("栋")[0].split("号")[0][:20] or "KML站点"

    if not placemarks:
        raise ValueError("KML 中未找到 Placemark 点位")

    return site_name, placemarks
