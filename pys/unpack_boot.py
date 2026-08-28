"""Boot image unpack — 解包 boot/vendor_boot 镜像。"""

import os
import shutil
from pathlib import Path

from pys.utils import V, BIN_PATH, display, call, rmdire
from pys.utils import gettype, findfile


def unpackboot(file, distance):
    """Unpack a boot image into a staging directory and report success."""
    original_dir = os.getcwd()
    work_dir = Path(distance)
    try:
        rmdire(work_dir)
        work_dir.mkdir(parents=True, exist_ok=False)
        shutil.copy2(file, work_dir / "boot_o.img")
        os.chdir(work_dir)
        if call(['magiskboot', 'unpack', '-h', str(file)]) != 0:
            print(f"Unpack {file} Fail...")
            return False

        ramdisk = work_dir / 'ramdisk.cpio'
        if not ramdisk.is_file() or V.SETUP_MANIFEST.get('BOOT_SKIP_RAMDISK', '0') == '1':
            print("Unpack Done!")
            return True

        comp = gettype(str(ramdisk))
        print(f"Ramdisk is {comp}")
        (work_dir / 'comp').write_text(comp, encoding='utf-8')
        if comp != 'unknown':
            compressed_ramdisk = work_dir / 'ramdisk.cpio.comp'
            os.replace(ramdisk, compressed_ramdisk)
            if call([
                'magiskboot',
                'decompress',
                str(compressed_ramdisk),
                str(ramdisk),
            ]) != 0:
                print("Decompress Ramdisk Fail...")
                return False

        ramdisk_dir = work_dir / 'ramdisk'
        ramdisk_dir.mkdir(exist_ok=True)
        print("Unpacking Ramdisk...")
        os.chdir(ramdisk_dir)
        if call(['magiskboot', 'cpio', str(work_dir / 'ramdisk.cpio'), 'extract']) != 0:
            print("Unpack Ramdisk Fail...")
            os.chdir(work_dir)
            return False
        os.chdir(work_dir)
        return True
    except OSError as error:
        print(f"Unpack {file} Fail: {error}")
        return False
    finally:
        os.chdir(original_dir)


def boot_unpack(source, distance):
    """Entry point for boot unpack."""
    if not os.path.isdir(distance):
        os.makedirs(distance)
    display(f"正在分解: {os.path.basename(source)}")
    return unpackboot(source, distance)
