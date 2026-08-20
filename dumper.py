import argparse
import atexit
import base64
import hashlib
import hmac
import json
import os
import random
import re
import secrets
import sys
import tempfile
import time
import zipfile
from io import BytesIO
from urllib.parse import urlsplit

import certifi
import requests
from pyaxmlparser import APK
from tqdm import tqdm

BASE = "https://backapi.rustore.ru"

# CDN RuStore (static-m.rustore.ru) отдаёт сертификат от НУЦ Минцифры, которого
# нет ни в certifi, ни в системном сторе — на чистой машине (например, на
# раннере GitHub Actions) скачивание падает с CERTIFICATE_VERIFY_FAILED.
# Доверяем этому корню точечно: только для хостов RuStore и только в дополнение
# к обычному набору, чтобы проверка сертификатов нигде не отключалась.
HERE = os.path.dirname(os.path.abspath(__file__))
RUSSIAN_CA = os.path.join(HERE, "certs", "russian_trusted_ca.pem")
TRUSTED_CA_SUFFIXES = (".rustore.ru",)

_ca_bundle_path = None


def ca_bundle():
    """certifi + корень НУЦ Минцифры одним файлом (склеивается при первом вызове)."""
    global _ca_bundle_path
    if _ca_bundle_path is None:
        if not os.path.exists(RUSSIAN_CA):
            return certifi.where()
        fd, path = tempfile.mkstemp(prefix="rustore-ca-", suffix=".pem")
        with os.fdopen(fd, "wb") as out:
            for src in (certifi.where(), RUSSIAN_CA):
                with open(src, "rb") as f:
                    out.write(f.read())
                out.write(b"\n")
        atexit.register(lambda: os.path.exists(path) and os.remove(path))
        _ca_bundle_path = path
    return _ca_bundle_path


def verify_for(url):
    """Расширенный набор корней — только для хостов RuStore, остальным дефолтный."""
    host = (urlsplit(url).hostname or "").lower()
    if any(host == s.lstrip(".") or host.endswith(s) for s in TRUSTED_CA_SUFFIXES):
        return ca_bundle()
    return True


# Профиль устройства, которым мы представляемся. Значения по умолчанию
# соответствуют RuStore 1.107.0.3 на современном Android-телефоне.
RUSTORE_VER_CODE = 1107003
RUSTORE_VER_NAME = "1.107.0.3"
DEFAULT_SDK = 35
DEFAULT_RELEASE = "15"
DEFAULT_ABIS = ["arm64-v8a", "armeabi-v7a", "armeabi"]
DEFAULT_DENSITY = 480
DEFAULT_LOCALES = ["ru", "en"]
DEFAULT_MANUFACTURER = "Google"
DEFAULT_MODEL = "Pixel 8"
DEFAULT_LANG = "ru"
DEFAULT_DEVICE_TYPE = "mobile"  # mobile | tv

# С августа 2026 backapi требует заголовок X-Client-Signature на всех запросах
# витрины/скачивания — без него ответ 419 с пустым телом. Подпись повторяет
# нативную libbridge_helper.so (com.ae.cf.bd.D) официального приложения:
#
#   X-Client-Signature = base64(HMAC_SHA256(SECURE_KEY, nonce || cert_sha256))
#
#   nonce       — 32 байта из POST api.rustore.ru/v1/secure/nonce (base64),
#   cert_sha256 — SHA-256 сертификата подписи самого RuStore (константа),
#   SECURE_KEY  — 32-байтный ключ, зашитый в libbridge_helper.so (константа,
#                 не зависит от устройства; снят реверсом нативной либы).
# Подпись живёт ~20 минут (TTL secure-сессии), поэтому считаем один раз на клиент.
SECURE_KEY = bytes.fromhex(
    "2be79e8826e75459d9ef528455a974839b221da5fabfa76b87cba978b8043e85"
)
RUSTORE_CERT_SHA256 = bytes.fromhex(
    "661f20828ef780de0b79bc59f26a30864316355f30e4f91cfa14a20791839914"
)
NONCE_URL = "https://api.rustore.ru/v1/secure/nonce"

RETRY_STATUSES = (419, 429, 500, 502, 503, 504)
API_ATTEMPTS = 8


class RuStore:
    """Клиент backapi.rustore.ru, повторяющий запросы оригинального приложения.

    Ключевой момент: RuStore раскатывает обновления постепенно и выбирает
    версию по заголовку deviceId. Запрос без deviceId получает последнюю
    версию, раскатанную на 100% аудитории, — из-за этого и казалось, что
    RuStore «выкладывает обновления поздно».
    """

    def __init__(self, device_id=None, sdk=DEFAULT_SDK, abis=None, density=DEFAULT_DENSITY,
                 locales=None, manufacturer=DEFAULT_MANUFACTURER, model=DEFAULT_MODEL,
                 lang=DEFAULT_LANG, release=DEFAULT_RELEASE, device_type=DEFAULT_DEVICE_TYPE):
        self.device_id = device_id or self._random_device_id()
        self.sdk = sdk
        self.abis = abis or list(DEFAULT_ABIS)
        self.density = density
        self.locales = locales or list(DEFAULT_LOCALES)
        self.manufacturer = manufacturer
        self.model = model
        self.lang = lang
        self.release = release
        self.device_type = device_type
        self._signature = None
        self.session = requests.Session()
        self.session.headers.update(self._device_headers())
        self.session.verify = verify_for(BASE)

    @staticmethod
    def _random_device_id():
        # RuStore шлёт deviceId в формате <androidId(16 hex)>-<число>, а не UUID.
        return f"{secrets.token_hex(8)}-{secrets.randbelow(4_000_000_000)}"

    def _user_agent(self):
        # RuStore/<verName> (Android <release>; SDK <sdk>; <abis>; <manufacturer> <model>; <lang>)
        return (f"RuStore/{RUSTORE_VER_NAME} (Android {self.release}; SDK {self.sdk}; "
                f"{', '.join(self.abis)}; {self.manufacturer} {self.model}; {self.lang})")

    def _device_headers(self):
        # Набор заголовков из DeviceInfoInterceptor оригинального приложения.
        return {
            "deviceId": self.device_id,
            "firmwareVer": self.release,
            "androidSdkVer": str(self.sdk),
            "deviceManufacturerName": self.manufacturer,
            "deviceModelName": self.model,
            "deviceModel": f"{self.manufacturer} {self.model}",
            "firmwareLang": self.lang,
            "ruStoreVerCode": str(RUSTORE_VER_CODE),
            "ruStoreVerName": RUSTORE_VER_NAME,
            "deviceType": self.device_type,
            "User-Agent": self._user_agent(),
        }

    def _client_signature(self):
        """X-Client-Signature: base64(HMAC_SHA256(SECURE_KEY, nonce || cert_sha256)).

        Кэшируется на клиент — одна подпись валидна весь TTL secure-сессии.
        deviceId между запросом nonce и подписанными запросами не меняем.
        """
        if self._signature is None:
            r = self._request("POST", NONCE_URL, json={},
                               headers={"Content-Type": "application/json"})
            nonce = base64.b64decode(r.json()["nonce"])
            mac = hmac.new(SECURE_KEY, nonce + RUSTORE_CERT_SHA256, hashlib.sha256).digest()
            self._signature = base64.b64encode(mac).decode()
        return self._signature

    def _request(self, method, url, attempts=API_ATTEMPTS, signed=False, **kwargs):
        """Запрос с повторами: 419/5xx и пустые ответы API отдаёт вперемешку с 200."""
        headers = dict(kwargs.pop("headers", None) or {})
        if signed:
            headers["X-Client-Signature"] = self._client_signature()
        last = None
        for i in range(attempts):
            try:
                r = self.session.request(method, url, timeout=30, headers=headers, **kwargs)
            except requests.RequestException as e:
                last = f"сеть: {e}"
            else:
                if r.status_code not in RETRY_STATUSES and r.content:
                    return r
                last = f"HTTP {r.status_code}" + ("" if r.content else " с пустым телом")
            if i + 1 < attempts:
                delay = min(1.0 * 1.6 ** i, 10) * (0.5 + random.random())
                print(f"  {last}, повтор {i + 2}/{attempts} через {delay:.1f} c")
                time.sleep(delay)
        raise RuntimeError(f"API не ответил за {attempts} попыток ({last})")

    def app_info(self, package_name):
        r = self._request("GET", f"{BASE}/applicationData/overallInfo/{package_name}", signed=True)
        data = r.json()
        if data.get("code") != "OK":
            return None
        return data["body"]

    def download_info(self, app_id, without_splits=True, first_install=True):
        """POST v3/showcase/apps/download-link — актуальный эндпоинт скачивания.

        Требует X-Client-Signature; v4 и старый applicationData/download-link
        с августа 2026 отвечают 419.
        """
        body = {
            "appId": app_id,
            "firstInstall": first_install,
            "mobileServices": [],
            "supportedAbis": self.abis,
            "screenDensity": self.density,
            "supportedLocales": self.locales,
            "sdkVersion": self.sdk,
            "withoutSplits": without_splits,
            "signatureFingerprints": None,
        }
        r = self._request(
            "POST",
            f"{BASE}/v3/showcase/apps/download-link",
            json=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            signed=True,
        )
        if r.status_code == 200 and r.content:
            data = r.json()
            return {
                "versionCode": data.get("versionCode"),
                "versionId": data.get("versionId"),
                "urls": [u["url"] for u in data.get("downloadUrls") or []],
            }
        print(f"v3 вернул HTTP {r.status_code}")
        return None


def pick_best_client(package_name, probes, **kwargs):
    """Опрашивает API несколькими deviceId и возвращает клиента с самой свежей версией.

    Во время частичной раскатки разные deviceId попадают в разные группы, так что
    перебор нескольких идентификаторов позволяет поймать обновление раньше.
    """
    best_client = None
    best_info = None
    answered = False
    for i in range(max(1, probes)):
        client = RuStore(**kwargs)
        try:
            info = client.app_info(package_name)
        except Exception as e:
            print(f"  проба {i + 1}: ошибка {e}")
            continue
        answered = True
        if info is None:
            return None, None
        vc = info.get("versionCode") or 0
        if probes > 1:
            print(f"  проба {i + 1}: {info.get('versionName')} ({vc})")
        if best_info is None or vc > (best_info.get("versionCode") or 0):
            best_client, best_info = client, info
    if best_info is None and not answered:
        raise RuntimeError("ни один запрос к API не удался")
    return best_client, best_info


def extract_apks(container, output_path):
    """Внутри ссылки лежит zip с одним .apk; иногда — сам apk."""
    container.seek(0)
    try:
        with zipfile.ZipFile(container) as z:
            apk_file = next((f for f in z.namelist() if f.endswith(".apk")), None)
            if apk_file is None:
                raise zipfile.BadZipFile
            with open(output_path, "wb") as f:
                f.write(z.read(apk_file))
            return True
    except zipfile.BadZipFile:
        container.seek(0)
        with open(output_path, "wb") as f:
            f.write(container.read())
        return False


def version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or ""))


def split_suffix(path):
    """base для основного APK, иначе имя split'а (config.arm64_v8a и т.п.)."""
    try:
        split = APK(path).get_android_manifest_xml().get("split")
    except Exception:
        return None
    return split or "base"


def download_rustore_apk(package_name, output_dir=".", probes=1, splits=False,
                         device_id=None, check_only=False, expect_version=None,
                         **device_kwargs):
    if probes > 1:
        print(f"Опрос {probes} устройств...")
    client, info = pick_best_client(package_name, probes if not device_id else 1,
                                    device_id=device_id, **device_kwargs)
    if info is None:
        print("Приложение не найдено")
        return None

    print(f"\n{'=' * 40}")
    print(f"Package:  {info.get('packageName')}")
    print(f"Version:  {info.get('versionName')} ({info.get('versionCode')})")
    print(f"Обновлено: {info.get('appVerUpdatedAt')}")
    print(f"Размер:   {info.get('fileSize')}")
    print(f"deviceId: {client.device_id}")
    print(f"{'=' * 40}")

    # Раскатка идёт по deviceId, поэтому без нужного идентификатора можно
    # молча скачать предыдущую версию и выложить её как релиз новой.
    # Версия новее ожидаемой — не проблема: значит, обновление успело выйти.
    got = info.get("versionName")
    if expect_version and version_tuple(got) < version_tuple(expect_version):
        print(f"Ожидалась версия {expect_version}, а API отдал {got} — прерываю, "
              f"чтобы не выложить старую сборку (deviceId не попал в раскатку?)")
        return None

    if check_only:
        return info

    link = client.download_info(info["appId"], without_splits=not splits)
    if link is None:
        print("Ошибка получения ссылки")
        return None
    if link["versionCode"] != info.get("versionCode"):
        print(f"Внимание: ссылка ведёт на versionCode {link['versionCode']}")

    os.makedirs(output_dir, exist_ok=True)
    saved = []
    for i, url in enumerate(link["urls"]):
        suffix = "" if len(link["urls"]) == 1 else f"_{i}"
        output_path = os.path.join(output_dir, f"{package_name}{suffix}.apk")
        try:
            response = requests.get(url, stream=True, timeout=60, verify=verify_for(url))
        except requests.exceptions.SSLError as e:
            host = urlsplit(url).hostname
            print(f"Не удалось проверить сертификат {host}: {e}")
            print(f"Если хост сменил УЦ, обновите {RUSSIAN_CA} или добавьте домен "
                  f"в TRUSTED_CA_SUFFIXES")
            return None
        response.raise_for_status()
        container = BytesIO()
        with tqdm(total=int(response.headers.get("content-length", 0)),
                  unit="B", unit_scale=True, desc=f"{package_name}{suffix}") as bar:
            for chunk in response.iter_content(8192):
                container.write(chunk)
                bar.update(len(chunk))
        extract_apks(container, output_path)
        if suffix:
            name = split_suffix(output_path)
            if name:
                renamed = os.path.join(output_dir, f"{package_name}_{name}.apk")
                os.replace(output_path, renamed)
                output_path = renamed
        saved.append(output_path)
        print(f"Сохранено: {output_path}")

    app_info = {
        "package": info.get("packageName"),
        "version_name": info.get("versionName"),
        "version_code": info.get("versionCode"),
        "version_id": info.get("versionId"),
        "app_ver_updated_at": info.get("appVerUpdatedAt"),
        "min_sdk_version": info.get("minSdkVersion"),
        "whats_new": info.get("whatsNew", "Информация отсутствует"),
        "device_id": client.device_id,
        "files": saved,
    }
    try:
        apk = APK(saved[0])
        app_info["target_sdk_version"] = apk.get_target_sdk_version()
        print(f"Target SDK: {app_info['target_sdk_version']}")
    except Exception as e:
        print(f"Предупреждение: {e}")

    with open(os.path.join(output_dir, "app_info.json"), "w", encoding="utf-8") as f:
        json.dump(app_info, f, ensure_ascii=False, indent=2)

    return saved


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Скачивание APK из RuStore")
    p.add_argument("package", nargs="?", default="ru.oneme.app")
    p.add_argument("-o", "--output-dir", default=".")
    p.add_argument("-n", "--probes", type=int, default=5,
                   help="сколько случайных deviceId опросить и взять самую свежую версию")
    p.add_argument("--device-id", help="фиксированный deviceId вместо случайных")
    p.add_argument("--expect-version",
                   help="ожидаемая versionName; при несовпадении выйти с ошибкой")
    p.add_argument("--splits", action="store_true",
                   help="скачать split-APK вместо универсального")
    p.add_argument("--check", action="store_true", help="только показать версию, без скачивания")
    p.add_argument("--sdk", type=int, default=DEFAULT_SDK)
    p.add_argument("--abis", default=",".join(DEFAULT_ABIS))
    p.add_argument("--density", type=int, default=DEFAULT_DENSITY)
    p.add_argument("--locales", default=",".join(DEFAULT_LOCALES))
    p.add_argument("--device-type", default=DEFAULT_DEVICE_TYPE, choices=["mobile", "tv"])
    a = p.parse_args()

    result = download_rustore_apk(
        a.package,
        output_dir=a.output_dir,
        probes=a.probes,
        splits=a.splits,
        device_id=a.device_id,
        check_only=a.check,
        expect_version=a.expect_version,
        sdk=a.sdk,
        abis=a.abis.split(","),
        density=a.density,
        locales=a.locales.split(","),
        device_type=a.device_type,
    )
    sys.exit(0 if result else 1)
