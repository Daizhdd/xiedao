# -*- mode: python ; coding: utf-8 -*-
import os
from PyInstaller.utils.hooks import copy_metadata

a = Analysis(
    ['app.py'], pathex=[], binaries=[],
    datas=[('assets/icon.ico', 'assets'), ('assets/brand-logo.png', 'assets'),
           ('LICENSE', '.'), ('THIRD_PARTY_NOTICES.md', '.'), ('licenses', 'licenses')]
          + copy_metadata('certifi'),
    hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=[], noarchive=False, optimize=0,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [], exclude_binaries=True, name='写道', debug=False,
    bootloader_ignore_signals=False, strip=False, upx=False, console=False,
    argv_emulation=False, target_arch=os.environ.get('XIEDAO_TARGET_ARCH'),
    codesign_identity=os.environ.get('XIEDAO_CODESIGN_IDENTITY'),
    entitlements_file=None,
)
collection = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='写道')
app = BUNDLE(
    collection, name='写道.app', icon='build/macos-icon.icns',
    bundle_identifier='io.github.daizhdd.xiedao',
    info_plist={
        'CFBundleName': '写道', 'CFBundleDisplayName': '写道',
        'CFBundleShortVersionString': '2026.10.05', 'CFBundleVersion': '2026.10.05',
        'NSHighResolutionCapable': True, 'LSMinimumSystemVersion': '13.0',
    },
)
