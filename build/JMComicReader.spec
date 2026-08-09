# -*- mode: python ; coding: utf-8 -*-

import os

# 本 spec 位于 build/ 下，项目根 = build/ 的父目录。
# 用 SPECPATH 计算绝对路径，确保从 build/ 移动/调用时仍能定位源码与资源。
ROOT = os.path.dirname(SPECPATH)


a = Analysis(
    [os.path.join(ROOT, 'desktop_app.py')],
    pathex=[],
    binaries=[],
    datas=[
        (os.path.join(ROOT, 'web'), 'web'),
        (os.path.join(ROOT, 'VERSION'), '.'),
    ],
    hiddenimports=[
        'webview',
        'webview.platforms.edgechromium',
        'webview.platforms.winforms',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'av',
        'cv2',
        'fastapi',
        'gradio',
        'librosa',
        'llvmlite',
        'matplotlib',
        'numba',
        'openpyxl',
        'paddle',
        'pandas',
        'scipy',
        'sklearn',
        'soundfile',
        'sqlalchemy',
        'sympy',
        'tensorboard',
        'tensorflow',
        'torch',
        'torchaudio',
        'torchvision',
        'transformers',
        'triton',
        'uvicorn',
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='JMComicReader',
    contents_directory='appdata',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=os.path.join(ROOT, 'web/static/img/app_icon.ico'),
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
    upx=False,
    upx_exclude=[],
    name='JMComicReader',
)
