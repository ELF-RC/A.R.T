"""Boot image repack — 打包 boot/vendor_boot 镜像。"""

import os
import subprocess
from pathlib import Path

from pys.utils import V, BIN_PATH, display, call
from pys.utils import gettype, findfile


def dboot(infile, dist):
    or_dir = os.getcwd()
    if not os.path.exists(infile):
        print(f"Cannot Find {infile}...")
        return
    if os.path.isdir(infile + os.sep + "ramdisk") and V.SETUP_MANIFEST.get('BOOT_SKIP_RAMDISK', '0') == '0':
        new_cpio = os.path.join(infile, "ramdisk-new.cpio")
        try:
            os.chdir(infile + os.sep + "ramdisk")
        except Exception as e:
            print("Ramdisk Not Found.. %s" % e)
            return
        busybox = findfile('busybox', BIN_PATH).replace('\\', "/")
        try:
            proc = subprocess.Popen(
                f'find . -mindepth 1 | {busybox} cpio -o -H newc -R 0:0 -F ../ramdisk-new.cpio',
                shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
            )
            stdout, _ = proc.communicate(timeout=120)
            cpio_rc = proc.returncode
        except (subprocess.TimeoutExpired, OSError) as e:
            print(f"cpio error: {e}")
            cpio_rc = 1
        os.chdir(infile)
        if cpio_rc != 0 or not os.path.isfile(new_cpio):
            print("Pack Ramdisk Fail... (cpio error)")
            os.chdir(or_dir)
            return
        print("Pack Ramdisk Successful..")
        try:
            os.remove("ramdisk.cpio")
        except OSError:
            pass
        os.rename("ramdisk-new.cpio", "ramdisk.cpio")
    else:
        os.chdir(infile)
    repack_args = ['magiskboot', 'repack', os.path.join(infile, "boot_o.img")]
    if call(repack_args) != 0:
        print("Pack boot Fail...")
        os.chdir(or_dir)
        return
    else:
        if os.path.exists(os.path.join(dist, os.path.basename(infile) + ".img")):
            os.remove(os.path.join(dist, os.path.basename(infile) + ".img"))
        os.rename(infile + os.sep + "new-boot.img", os.path.join(dist, os.path.basename(infile) + ".img"))
        os.chdir(or_dir)
        print("Pack Successful...")


def boot_repack(source, distance):
    """Entry point for boot repack."""
    if not os.path.isdir(distance):
        os.makedirs(distance)
    display(f"重新合成: {os.path.basename(source)}.img")
    return dboot(source, distance)
