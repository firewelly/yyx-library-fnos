#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
构建 fnOS (.fpk) 安装包。

.fpk 就是纯 tar.gz（无需 fnpack）：
  1. app 载荷（Flutter web 产物 + webserver + ui/config）→ app.tgz
  2. app.tgz 的 sha256 写入 manifest 的 checksum 字段
  3. 包根（manifest/cmd/config/wizard/ICON/app.tgz）→ novelmgt.fpk

用法（在 novelmgt_flutter/ 下）：
    python tool/fnos/build_fpk.py                 # 需先 flutter build web --release
    python tool/fnos/build_fpk.py --out dist/xx.fpk
"""

import argparse
import hashlib
import os
import shutil
import sys
import tarfile
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))  # novelmgt_flutter/
PAYLOAD = os.path.join(HERE, "payload")
FPK_ROOT = os.path.join(HERE, "fpk_root")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def add_file(tar, path, arcname, mode):
    info = tar.gettarinfo(path, arcname=arcname)
    info.mode = mode
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    with open(path, "rb") as f:
        tar.addfile(info, f)


def add_dir(tar, dirpath, arcname_prefix, file_mode=0o644):
    for root, _dirs, files in os.walk(dirpath):
        rel = os.path.relpath(root, dirpath).replace(os.sep, "/")
        rel = "" if rel == "." else rel
        for name in sorted(files):
            full = os.path.join(root, name)
            arc = "./%s/%s" % (rel, name) if rel else "./%s" % name
            add_file(tar, full, arc, file_mode)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--web-dir", default=os.path.join(ROOT, "build", "web"))
    ap.add_argument("--out", default=os.path.join(ROOT, "dist", "novelmgt-fnos.fpk"))
    args = ap.parse_args()

    web_dir = os.path.abspath(args.web_dir)
    if not os.path.isdir(web_dir):
        print("缺少 Flutter web 构建产物: %s\n先执行 flutter build web --release" % web_dir)
        return 1

    stage = tempfile.mkdtemp(prefix="novelmgt_fpk_")
    try:
        # ---- 1. 组装 app 载荷 ----
        app_dir = os.path.join(stage, "app")
        os.makedirs(os.path.join(app_dir, "bin"))
        ui_dir = os.path.join(app_dir, "ui")
        shutil.copytree(web_dir, ui_dir)
        # 桌面入口配置覆盖
        shutil.copy(os.path.join(PAYLOAD, "ui", "config"), os.path.join(ui_dir, "config"))
        # icons: ui/images/icon-{0}.png 由 web/icons 提供
        images = os.path.join(ui_dir, "images")
        os.makedirs(images, exist_ok=True)
        for src_name, dst_name in (("Icon-192.png", "icon-192.png"), ("Icon-512.png", "icon-512.png")):
            src = os.path.join(ROOT, "web", "icons", src_name)
            if os.path.isfile(src):
                shutil.copy(src, os.path.join(images, dst_name))
        shutil.copy(os.path.join(PAYLOAD, "bin", "novel_webserver.py"),
                    os.path.join(app_dir, "bin", "novel_webserver.py"))

        # ---- 2. app.tgz ----
        app_tgz = os.path.join(stage, "app.tgz")
        with tarfile.open(app_tgz, "w:gz") as tar:
            add_file(tar, os.path.join(app_dir, "bin", "novel_webserver.py"),
                     "./bin/novel_webserver.py", 0o755)
            # ui 根目录文件（config 用 payload 版覆盖，其余来自 build/web）
            for name in sorted(os.listdir(ui_dir)):
                full = os.path.join(ui_dir, name)
                if os.path.isfile(full):
                    arc = "./ui/%s" % name
                    add_file(tar, full, arc, 0o644)
            add_file(tar, os.path.join(PAYLOAD, "ui", "config"), "./ui/config", 0o644)
            # ui 子目录（canvaskit/、sqlite_http/、assets/、icons/、images/ ...）
            for root, _dirs, files in os.walk(ui_dir):
                rel = os.path.relpath(root, ui_dir).replace(os.sep, "/")
                if rel == ".":
                    continue
                for name in sorted(files):
                    full = os.path.join(root, name)
                    arc = "./ui/%s/%s" % (rel, name)
                    add_file(tar, full, arc, 0o644)

        checksum = sha256_file(app_tgz)

        # ---- 3. 包根 + manifest checksum ----
        root_stage = os.path.join(stage, "fpk")
        shutil.copytree(FPK_ROOT, root_stage)
        manifest_path = os.path.join(root_stage, "manifest")
        with open(manifest_path, "r", encoding="utf-8") as f:
            manifest = f.read()
        manifest = manifest.replace("checksum        = TO_BE_FILLED_BY_BUILD",
                                    "checksum        = %s" % checksum)
        with open(manifest_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(manifest)
        shutil.copy(app_tgz, os.path.join(root_stage, "app.tgz"))
        # 图标
        for src_name, dst_name in (("Icon-192.png", "ICON.PNG"), ("Icon-512.png", "ICON_256.PNG")):
            src = os.path.join(ROOT, "web", "icons", src_name)
            if os.path.isfile(src):
                shutil.copy(src, os.path.join(root_stage, dst_name))

        # ---- 4. novelmgt.fpk ----
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with tarfile.open(args.out, "w:gz") as tar:
            add_file(tar, manifest_path, "./manifest", 0o644)
            for name in ("ICON.PNG", "ICON_256.PNG", "app.tgz"):
                p = os.path.join(root_stage, name)
                if os.path.isfile(p):
                    add_file(tar, p, "./%s" % name, 0o644)
            for name in sorted(os.listdir(os.path.join(root_stage, "cmd"))):
                add_file(tar, os.path.join(root_stage, "cmd", name),
                         "./cmd/%s" % name, 0o755)
            for sub in ("config", "wizard"):
                for name in sorted(os.listdir(os.path.join(root_stage, sub))):
                    add_file(tar, os.path.join(root_stage, sub, name),
                             "./%s/%s" % (sub, name), 0o644)

        print("OK: %s (%.1f MB, app.tgz sha256=%s)"
              % (args.out, os.path.getsize(args.out) / 1e6, checksum[:16] + "..."))
        return 0
    finally:
        shutil.rmtree(stage, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
