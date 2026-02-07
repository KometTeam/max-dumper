import requests
import zipfile
import os
import sys
import json
from io import BytesIO
from pyaxmlparser import APK
from tqdm import tqdm

def download_rustore_apk(package_name, output_dir="."):
    info = requests.get(f"https://backapi.rustore.ru/applicationData/overallInfo/{package_name}").json()
    if info.get("code") != "OK":
        print("Приложение не найдено")
        return None

    whats_new = info.get("body", {}).get("whatsNew", "Информация отсутствует")
    
    download_data = requests.post(
        "https://backapi.rustore.ru/applicationData/download-link",
        json={"appId": info["body"]["appId"], "firstInstall": True},
        headers={"Content-Type": "application/json; charset=utf-8"}
    ).json()
    
    if download_data.get("code") != "OK":
        print("Ошибка получения ссылки")
        return None
    
    print("Скачивание...")
    response = requests.get(download_data["body"]["apkUrl"], stream=True)
    container = BytesIO()
    
    with tqdm(total=int(response.headers.get('content-length', 0)), 
              unit='B', unit_scale=True, desc=package_name) as bar:
        for chunk in response.iter_content(8192):
            container.write(chunk)
            bar.update(len(chunk))
    
    container.seek(0)
    output_path = os.path.join(output_dir, f"{package_name}.apk")
    
    try:
        with zipfile.ZipFile(container) as z:
            apk_file = next((f for f in z.namelist() if f.endswith('.apk')), None)
            if apk_file:
                with open(output_path, 'wb') as f:
                    f.write(z.read(apk_file))
                print(f"APK извлечен из архива")
            else:
                raise zipfile.BadZipFile
    except zipfile.BadZipFile:
        container.seek(0)
        with open(output_path, 'wb') as f:
            f.write(container.read())
    
    try:
        apk = APK(output_path)
        
        app_info = {
            "package": apk.package,
            "version_name": apk.version_name,
            "version_code": apk.version_code,
            "min_sdk_version": apk.get_min_sdk_version(),
            "target_sdk_version": apk.get_target_sdk_version(),
            "whats_new": whats_new
        }
        
        print(f"\n{'='*40}")
        print(f"Package: {app_info['package']}")
        print(f"Version: {app_info['version_name']} ({app_info['version_code']})")
        print(f"Min SDK: {app_info['min_sdk_version']}")
        print(f"Target SDK: {app_info['target_sdk_version']}")
        print(f"{'='*40}")
        
        with open('app_info.json', 'w', encoding='utf-8') as f:
            json.dump(app_info, f, ensure_ascii=False, indent=2)
        
    except Exception as e:
        print(f"Предупреждение: {e}")
    
    print(f"Сохранено: {output_path}")
    return output_path

if __name__ == "__main__":
    download_rustore_apk(sys.argv[1] if len(sys.argv) > 1 else "ru.oneme.app")
