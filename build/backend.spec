# -*- mode: python ; coding: utf-8 -*-
"""Сборка Python-бэкенда в один исполняемый файл.

    npm run build:backend

Результат — `build/dist/backend/parlament-backend[.exe]`. Отдельная папка
нужна, чтобы не пересекаться с выводом electron-builder в `dist/`.
Electron-builder кладёт её в ресурсы приложения (см. `extraResources` в
package.json), а electron/backend.js запускает бэкенд оттуда.

Бэкенд не тянет сторонних пакетов, поэтому ни hidden imports, ни hooks
здесь не нужны.
"""

import os

project_root = os.path.abspath(os.path.join(os.getcwd()))

a = Analysis(
    [os.path.join(project_root, 'backend', 'main.py')],
    pathex=[os.path.join(project_root, 'backend')],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Всё это в бэкенде не используется и только раздувает сборку.
        'tkinter', 'unittest', 'pydoc', 'doctest', 'email', 'http',
        'xml', 'pdb', 'sqlite3', 'ssl', 'lib2to3',
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='parlament-backend',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    # Консольное окно не нужно: процесс общается через stdin/stdout,
    # и на Windows иначе мигала бы чёрная консоль.
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='backend',
)
