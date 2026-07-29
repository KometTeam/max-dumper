# MAX Dumper

Автоматический дамп мессенджера MAX APK через GitHub Actions.

## Что делает

Скачивает APK, извлекает метаданные и создаёт релиз на GitHub.

## Использование

### Локально
```bash
pip install -r requirements.txt
python dumper.py com.example.app
```

По умолчанию качается `ru.oneme.app` (MAX). Результат — `<package>.apk` и
`app_info.json` с версией, min/target SDK и списком изменений.

## dumper.py — RuStore

Скрипт повторяет запросы оригинального приложения RuStore (разобраны из
`RuStore-mobile-1.106.0.3.apk`, класс `qk2.b` / `DeviceInfoInterceptor` и
`l42.j` / `DownloadInfoApi`).

### Почему нужен deviceId

RuStore раскатывает обновления постепенно и выбирает версию по заголовку
`deviceId`. Запрос без него получает последнюю версию, раскатанную на 100%
аудитории, — из-за этого казалось, что RuStore выкладывает обновления с
опозданием на несколько дней:

| Запрос | Версия MAX | Дата |
| --- | --- | --- |
| только `ruStoreVerCode` | 26.23.2 (6779) | 17.07.2026 |
| + `deviceId` | 26.24.0 (6784) | 22.07.2026 |

`deviceId` — случайный UUID, генерируется на каждый запуск. `ruStoreVerCode`
обязателен: без него API отдаёт пустой HTTP 400. Остальные заголовки
(`androidSdkVer`, `deviceModel`, `deviceType`, `User-Agent` и т.д.) на выдачу
версии не влияют, но отправляются для достоверности.

### Ключи

```
python dumper.py [package] [опции]

  -o, --output-dir DIR   куда сохранять (по умолчанию текущая папка)
  -n, --probes N         опросить N случайных deviceId и взять самую свежую
                         версию (по умолчанию 5). Во время частичной раскатки
                         разные deviceId попадают в разные группы, так что
                         перебор ловит обновление раньше
      --device-id UUID   фиксированный deviceId вместо случайных
      --splits           split-APK вместо универсального
      --check            только показать версию, без скачивания
      --sdk N            androidSdkVer / sdkVersion (по умолчанию 35)
      --abis LIST        поддерживаемые ABI через запятую
      --density N        плотность экрана (по умолчанию 480)
      --locales LIST     локали через запятую
      --device-type T    mobile | tv
```

Проверить версию, ничего не качая:

```bash
python dumper.py ru.oneme.app --check
```

### Универсальный APK и splits

Без флагов в запрос идёт `withoutSplits: true` и RuStore отдаёт один universal
APK со всеми ABI и плотностями — то, что нужно для дампа. С `--splits`
приходит набор из base и config-модулей, как их ставит само приложение; файлы
именуются по манифесту:

```
ru.oneme.app_base.apk
ru.oneme.app_config.arm64_v8a.apk
ru.oneme.app_config.xxhdpi.apk
```

### API

Основной эндпоинт — `POST v4/showcase/apps/download-link` (тело
`DownloadInfoV4RequestDto`: `appId`, `firstInstall`, `supportedAbis`,
`screenDensity`, `supportedLocales`, `sdkVersion`, `withoutSplits`,
`signatureFingerprints`, `baseApkHash`). Если он отвечает не 200, скрипт
откатывается на старый `applicationData/download-link` — тот тоже уважает
`deviceId`. Информация о приложении берётся из
`GET applicationData/overallInfo/{packageName}`.

Ссылки ведут на zip, внутри которого лежит собственно APK; скрипт распаковывает
его автоматически.

## Остальные скрипты

- `dumper_appgallery.py <APP_ID>` — дамп из Huawei AppGallery через публичный
  appdl-редирект (по умолчанию `C113469599` — MAX).
- `pms_extract.py <app.apk | decoded_dir>` — вытаскивает определения PmsKey из
  MAX независимо от обфускации ProGuard/R8, результат в `pmskeys.json`.

## Workflows

- `dump-apk.yml` — дамп из RuStore, патч через apk-mitm, релиз на GitHub.
- `dump-apk-appgallery.yml` — то же для AppGallery, принимает `app_id`.
- `parser.yml` — по публикации релиза декомпилирует APK и обновляет
  `pmskeys.json`.
