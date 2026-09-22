"""DAT/DAT.BR repack — 将分区镜像转换为 block package。"""

import os
import tempfile

from scripts.primary import blockimgdiff, sparse_img
from scripts.primary.utils import (
    CLOSE,
    GREEN,
    RED,
    V,
    call,
    display,
)


def _image_to_dat(input_image, outdir='.', version=None, prefix='system'):
    """Convert an image into Android block OTA package files."""
    version_text = '1.7'
    print('img2sdat binary - version: %s\n' % version_text)

    if not os.path.isdir(outdir):
        os.makedirs(outdir)

    output_prefix = outdir + '/' + prefix
    blockimgdiff.BlockImageDiff(
        sparse_img.SparseImage(input_image, tempfile.mkstemp()[1], '0'),
        None,
        version,
    ).Compute(output_prefix)

    print('Done! Output files: %s' % os.path.dirname(output_prefix))


def recompress_dat_br(label, distance, flag):
    """Generate a .new.dat or .new.dat.br package from an image."""
    if flag <= 9:
        return

    display(f"重新生成: {label}.new.dat ...", 3)
    _image_to_dat(distance, V.out, 4, label)
    newdat = os.path.join(V.out, f"{label}.new.dat")
    if not os.path.isfile(newdat):
        print(f" {RED}打包失败{CLOSE}")
        return
    print(" Done")
    os.remove(distance)
    if flag == 11:
        level = V.SETUP_MANIFEST["REPACK_BR_LEVEL"]
        display(f"重新生成: {label}.new.dat.br | Level={level} ...", 3)
        newdat_brotli = f"{newdat}.br"
        call(["brotli", f"-{level}jfo", newdat_brotli, newdat])
        print(
            f" {GREEN}打包成功{CLOSE}"
            if os.path.isfile(newdat_brotli)
            else f" {RED}打包失败{CLOSE}"
        )
