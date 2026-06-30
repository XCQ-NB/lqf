"""从 Excel/CSV 行数据解析站点点位（输出结构与 KML 一致）。"""
import re


def _norm(text):
    return re.sub(r"\s+", "", str(text or "").strip().lower())


SITE_ALIASES = ("站点", "站点名称", "站名", "site", "site_name", "sitename")
POINT_ALIASES = ("点位", "点位名称", "名称", "name", "point", "pointname")
LNG_ALIASES = ("经度", "longitude", "lng", "lon", "x")
LAT_ALIASES = ("纬度", "latitude", "lat", "y")
DESC_ALIASES = ("地址", "描述", "详情", "description", "备注", "说明")


def _find_col(headers, aliases):
    best_idx = -1
    best_score = -1
    for i, h in enumerate(headers):
        nh = _norm(h)
        if not nh:
            continue
        for a in aliases:
            na = _norm(a)
            if not na:
                continue
            if nh == na:
                score = 100 + len(na)
            elif len(na) >= 2 and na in nh:
                score = len(na)
            else:
                continue
            if score > best_score:
                best_score = score
                best_idx = i
    return best_idx


def _cell(row, idx):
    if idx < 0 or idx >= len(row):
        return ""
    v = row[idx]
    return str(v).strip() if v is not None else ""


def _parse_float(raw):
    try:
        return float(str(raw).replace(",", "").strip())
    except (TypeError, ValueError):
        return 0.0


def parse_excel_rows(rows, default_site_name=""):
    """rows: 二维数组，首行为表头。返回 (site_name, placemarks)。"""
    if not rows or len(rows) < 2:
        raise ValueError("表格至少需要表头行和一行数据")

    headers = [str(c or "").strip() for c in rows[0]]
    data_rows = rows[1:]
    site_col = _find_col(headers, SITE_ALIASES)
    point_col = _find_col(headers, POINT_ALIASES)
    lng_col = _find_col(headers, LNG_ALIASES)
    lat_col = _find_col(headers, LAT_ALIASES)
    desc_col = _find_col(headers, DESC_ALIASES)

    if point_col < 0:
        raise ValueError("表格需包含「点位名称」列（或 name / 名称）")

    placemarks = []
    site_names = set()

    for row in data_rows:
        if not row or all(str(c or "").strip() == "" for c in row):
            continue
        name = _cell(row, point_col)
        if not name:
            continue
        site = _cell(row, site_col) if site_col >= 0 else ""
        if site:
            site_names.add(site)
        lng = _parse_float(_cell(row, lng_col)) if lng_col >= 0 else 0.0
        lat = _parse_float(_cell(row, lat_col)) if lat_col >= 0 else 0.0
        desc = _cell(row, desc_col) if desc_col >= 0 else ""
        placemarks.append(
            {
                "name": name,
                "description": desc,
                "longitude": lng,
                "latitude": lat,
            }
        )

    if not placemarks:
        raise ValueError("表格中未找到有效点位行")

    if len(site_names) == 1:
        site_name = next(iter(site_names))
    elif len(site_names) > 1:
        site_name = default_site_name or next(iter(site_names))
    else:
        site_name = (default_site_name or "").strip()

    if not site_name:
        site_name = placemarks[0]["name"].split("栋")[0].split("号")[0][:20] or "Excel站点"

    return site_name, placemarks
