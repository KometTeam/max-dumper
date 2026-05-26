"""
Дамп APK мессенджера MAX (или любого приложения) из Huawei AppGallery
через публичный appdl-редирект.

Использование:
    python dumper_appgallery.py <APP_ID>          # напр. C113469599
    python dumper_appgallery.py                   # по умолчанию C113469599 (MAX)
"""

import json
import os
import re
import sys
from urllib.parse import urlparse, unquote

import requests
from pyaxmlparser import APK
from tqdm import tqdm

DEFAULT_APP_ID = "C113469599"
APPDL_URL      = "https://appgallery.cloud.huawei.com/appdl/{app_id}"
API_BASE       = "https://web-drcn.hispace.dbankcloud.com"
UA             = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36"


def fetch_whats_new(app_id: str) -> str:
    """
    Достаёт changelog ("Обновления") из подписанного AppGallery API.
    Возвращает текст или пустую строку, если не получилось.
    """
    import time as _t
    try:
        s = requests.Session()
        r = s.get(f"{API_BASE}/webedge/getInterfaceCode",
                  headers={"User-Agent": UA,
                           "Referer": "https://appgallery.huawei.com/"},
                  timeout=10)
        r.raise_for_status()
        code = r.text.strip().strip('"')
        ts = int(_t.time() * 1000)
        r = s.get(f"{API_BASE}/uowap/index", params={
            "method":      "internal.getTabDetail",
            "serviceType": "20",
            "reqPageNum":  "1",
            "maxResults":  "25",
            "uri":         f"app|{app_id}",
            "locale":      "ru",
        }, headers={
            "User-Agent":     UA,
            "Referer":        "https://appgallery.huawei.com/",
            "interface-code": f"{code}_{ts}",
        }, timeout=15)
        r.raise_for_status()
        data = r.json()
        if data.get("rtnCode") != 0:
            return ""
        for layout in data.get("layoutData", []):
            if layout.get("layoutName") == "detailprizecard":
                items = layout.get("dataList") or []
                if items:
                    return (items[0].get("body") or "").strip()
        return ""
    except Exception as e:
        print(f"Не удалось получить whats_new: {e}", file=sys.stderr)
        return ""


def download_appgallery_apk(app_id: str = DEFAULT_APP_ID, output_dir: str = "."):
    print(f"Резолвлю appdl для {app_id} ...")
    r = requests.get(
        APPDL_URL.format(app_id=app_id),
        headers={"User-Agent": UA},
        allow_redirects=True,
        stream=True,
        timeout=30,
    )
    r.raise_for_status()

    apk_url = r.url
    filename = unquote(urlparse(apk_url).path.rsplit("/", 1)[-1])
    m = re.match(r"(?P<package>[\w.]+)\.(?P<release_id>\d+)\.apk$", filename)
    if not m:
        print(f"Не разобрал имя файла: {filename}", file=sys.stderr)
        return None
    package = m["package"]

    print(f"Источник: {apk_url}")
    total = int(r.headers.get("Content-Length", 0))
    output_path = os.path.join(output_dir, f"{package}.apk")

    with open(output_path, "wb") as f, tqdm(
        total=total, unit="B", unit_scale=True, desc=package
    ) as bar:
        for chunk in r.iter_content(8192):
            if not chunk:
                continue
            f.write(chunk)
            bar.update(len(chunk))

    try:
        apk = APK(output_path)

        whats_new = fetch_whats_new(app_id) or "Информация отсутствует"

        app_info = {
            "package":            apk.package,
            "version_name":       apk.version_name,
            "version_code":       apk.version_code,
            "min_sdk_version":    apk.get_min_sdk_version(),
            "target_sdk_version": apk.get_target_sdk_version(),
            "whats_new":          whats_new,
            "source":             "appgallery",
            "appgallery_id":      app_id,
        }

        print(f"\n{'='*40}")
        print(f"Source:     AppGallery ({app_id})")
        print(f"Package:    {app_info['package']}")
        print(f"Version:    {app_info['version_name']} ({app_info['version_code']})")
        print(f"Min SDK:    {app_info['min_sdk_version']}")
        print(f"Target SDK: {app_info['target_sdk_version']}")
        print(f"{'='*40}")

        with open("app_info.json", "w", encoding="utf-8") as f:
            json.dump(app_info, f, ensure_ascii=False, indent=2)

    except Exception as e:
        print(f"Предупреждение: {e}", file=sys.stderr)

    print(f"Сохранено: {output_path}")
    return output_path


if __name__ == "__main__":
    download_appgallery_apk(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_APP_ID)
