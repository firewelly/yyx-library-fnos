#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键部署 novelmgt-fnos.fpk 到 misc (fnOS)。

流程：SFTP 上传 fpk → appcenter-cli 安装（失败自动 sudo 重试）→
校验服务端口 / NFS 书库可读 → 输出访问地址。

用法：
    python tool/fnos/deploy_misc.py --host 192.168.110.102 --user firewell --password '...'
    python tool/fnos/deploy_misc.py --fpk dist/novelmgt-fnos.fpk --skip-upload  # 包已在 misc:/tmp
"""

import argparse
import os
import sys
import time

import paramiko

DEFAULT_FPK = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "dist",
                 "novelmgt-fnos.fpk"))
REMOTE_FPK = "/tmp/novelmgt-fnos.fpk"
APP_PORT = 12702
LIB_PATH = "/mnt/dx4600/hermes3/novelmgt/novelmgt.db"


def run(c, cmd, timeout=120, input_text=None):
    _, out, err = c.exec_command(cmd, timeout=timeout)
    if input_text:
        chan = out.channel
        chan.send(input_text + "\n")
    o = out.read().decode("utf-8", "replace")
    e = err.read().decode("utf-8", "replace")
    rc = out.channel.recv_exit_status()
    return rc, o, e


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.110.102")
    ap.add_argument("--user", default="firewell")
    ap.add_argument("--password", required=True)
    ap.add_argument("--fpk", default=DEFAULT_FPK)
    ap.add_argument("--skip-upload", action="store_true")
    args = ap.parse_args()

    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print("[1/5] 连接 %s@%s ..." % (args.user, args.host))
    c.connect(args.host, username=args.user, password=args.password,
              timeout=15, banner_timeout=30, auth_timeout=30)
    _, out, _ = c.exec_command("whoami; hostname")
    print("  已连接:", out.read().decode().strip().replace("\n", " @ "))

    sudo_prefix = ""
    rc, o, e = run(c, "appcenter-cli --help >/dev/null 2>&1; echo $?")
    if rc != 0:
        sudo_prefix = "sudo -S "
        print("  appcenter-cli 需要 sudo，后续命令走 sudo")

    # ---- 上传 ----
    if not args.skip_upload:
        print("[2/5] SFTP 上传 %s -> %s:%s" % (args.fpk, args.host, REMOTE_FPK))
        sftp = c.open_sftp()
        sftp.put(args.fpk, REMOTE_FPK)
        st = sftp.stat(REMOTE_FPK)
        print("  远端大小: %d bytes" % st.st_size)
        local_size = os.path.getsize(args.fpk)
        assert st.st_size == local_size, "上传大小不一致!"
        sftp.close()
    else:
        print("[2/5] 跳过上传 (--skip-upload)")

    # ---- 校验 NFS 书库 ----
    print("[3/5] 校验 NFS 书库可读: %s" % LIB_PATH)
    rc, o, e = run(c, "ls -la %s 2>&1; head -c 16 %s | od -An -tx1 | head -1"
                   % (LIB_PATH, LIB_PATH))
    print(" ", o.strip()[:300] or e.strip()[:300])

    # ---- 安装 ----
    print("[4/5] 安装 fpk ...")
    cmd = "%sappcenter-cli install-fpk %s" % (sudo_prefix, REMOTE_FPK)
    rc, o, e = run(c, cmd, timeout=600,
                   input_text=args.password if sudo_prefix else None)
    print(" ", (o + e).strip()[-1500:] or "(无输出)")
    if rc != 0:
        # 权限不足时改走 sudo 再试一次
        if sudo_prefix == "":
            print("  直装失败(rc=%d)，改用 sudo 重试 ..." % rc)
            rc, o, e = run(c, "sudo -S appcenter-cli install-fpk %s" % REMOTE_FPK,
                           timeout=600, input_text=args.password)
            print(" ", (o + e).strip()[-1500:] or "(无输出)")
    if rc != 0:
        print("安装失败（ rc=%d ）。可手动：应用商店 → 手动安装 → 选择 %s" % (rc, REMOTE_FPK))
        return 1

    # ---- 验证 ----
    print("[5/5] 等待服务启动并验证 ...")
    for i in range(12):
        time.sleep(5)
        rc, o, _ = run(c, "ss -tln 2>/dev/null | grep -q :%d && echo LISTEN" % APP_PORT)
        if "LISTEN" in o:
            print("  端口 %d 已监听 (等待 %ds)" % (APP_PORT, (i + 1) * 5))
            break
    else:
        print("  警告: 端口 %d 尚未监听，查看日志: cat /var/apps/novelmgt/var/*.log" % APP_PORT)

    _, out, _ = c.exec_command(
        "curl -s -o /dev/null -w '%%{http_code}' http://127.0.0.1:%d/index.html; "
        "echo; cat /var/apps/novelmgt/var/db.conf 2>/dev/null; echo; "
        "cat /var/apps/novelmgt/var/state.conf 2>/dev/null" % APP_PORT, timeout=20)
    parts = (out.read().decode().split("\n") + ["", "", ""])[:3]
    print("  本机自检 index=%s | db.conf=%s | state.conf=%s" % tuple(parts))

    print("\n完成。访问方式：")
    print("  - fnOS 桌面/手机 App：点「小说管理」图标")
    print("  - 局域网直接访问: http://%s:%d/index.html?nas=1" % (args.host, APP_PORT))
    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
