import codecs
import json
import mmap
import os
import re
import struct
from pathlib import Path

from pys import ext4

SPARSE_HEADER_MAGIC = 0xED26FF3A
EXT4_RAW_HEADER_MAGIC = 0xED26FF3A
EXT4_SPARSE_HEADER_LEN = 28
EXT4_CHUNK_HEADER_SIZE = 12
LP_METADATA_HEADER_MAGIC = 1095520304
EROFS_HEADER_MAGIC = 0xE0F5E1E2


class ImageExtractionError(RuntimeError):
    """Raised when an image cannot be extracted without data loss."""


class EXT4_IMAGE_HEADER(object):

    def __init__(self, buf):
        (self.magic, self.major, self.minor, self.file_header_size, self.chunk_header_size, self.block_size,
         self.total_blocks, self.total_chunks, self.crc32) = struct.unpack('<I4H4I', buf)


class EXT4_CHUNK_HEADER(object):

    def __init__(self, buf):
        (self.type, self.reserved, self.chunk_size, self.total_size) = struct.unpack('<2H2I', buf)


def is_valid_ext4_directory_entry(entry_name, entry_inode_idx):
    """Return whether an EXT4 directory entry points to a real filesystem node."""
    return (
        entry_inode_idx != 0
        and isinstance(entry_name, str)
        and entry_name not in {'', '.', '..'}
    )


class ULTRAMAN(object):

    def __init__(self):
        self.FileName = ''
        self.BASE_DIR = ''
        self.OUTPUT_IMAGE_FILE = ''
        self.EXTRACT_DIR = ''
        self.contexts = []
        self.fsconfig = []
        self.space = []

    def __file_name(self, file_path):
        name = os.path.basename(file_path).split('.img')[0]
        name = name.split('.unsparse')[0]
        name = name.replace('/', '\\')
        return name

    @staticmethod
    def __appendf(msg, log):
        Path(log).parent.mkdir(parents=True, exist_ok=True)
        with open(log, 'w', encoding='utf-8', newline='\n') as file:
            print(msg, file=file)

    def __getperm(self, arg):
        if len(arg) < 9 or len(arg) > 10:
            return
        if len(arg) > 8:
            arg = arg[1:]
        oor, ow, ox, gr, gw, gx, wr, ww, wx = list(arg)
        o, g, w, s = 0, 0, 0, 0
        if oor == 'r': o += 4
        if ow == 'w': o += 2
        if ox == 'x': o += 1
        if ox == 'S': s += 4
        if ox == 's': s += 4; o += 1
        if gr == 'r': g += 4
        if gw == 'w': g += 2
        if gx == 'x': g += 1
        if gx == 'S': s += 2
        if gx == 's': s += 2; g += 1
        if wr == 'r': w += 4
        if ww == 'w': w += 2
        if wx == 'x': w += 1
        if wx == 'T': s += 1
        if wx == 't': s += 1; w += 1
        return str(s) + str(o) + str(g) + str(w)

    def checkSignOffset(self, file):
        size = os.stat(file.name).st_size
        length = 0 if size <= 52428800 else 52428800
        with mmap.mmap(file.fileno(), length, access=mmap.ACCESS_READ) as mm:
            return mm.find(struct.pack('<L', EXT4_RAW_HEADER_MAGIC))

    def __ImgSizeFromSparseFile(self, target):
        img_file = open(target, 'rb')

        if self.sign_offset > 0:
            img_file.seek(self.sign_offset, 0)

        header = EXT4_IMAGE_HEADER(img_file.read(28))
        imgsize = header.block_size * header.total_blocks
        img_file.close()

        return imgsize

    @staticmethod
    def __ImgSizeFromRawFile(target):
        with open(target, 'rb') as img_file:
            m = ''
            see = 1028

            for i in reversed(range(4)):
                img_file.seek(see + i)
                m += img_file.read(1).hex()

            imgsize = int('0x' + m, 16) * 4096

        return imgsize

    def GetImageType(self, target):
        filename, file_extension = os.path.splitext(target)
        if file_extension == '.img':
            with open(target, "rb") as img_file:
                setattr(self, 'sign_offset', self.checkSignOffset(img_file))
                if self.sign_offset > 0:
                    img_file.seek(self.sign_offset, 0)
                header = EXT4_IMAGE_HEADER(img_file.read(28))
                if header.magic != EXT4_RAW_HEADER_MAGIC:
                    return 'img'
                else:
                    return 'simg'

    def FIX_MOTO(self, input_file):
        if not os.path.exists(input_file):
            return
        output_file = input_file + "_"
        if os.path.exists(output_file):
            try:
                os.remove(output_file)
            except:
                pass
        with open(input_file, 'rb') as f:
            data = f.read(500000)
        moto = re.search(b'\x4d\x4f\x54\x4f', data)
        if not moto:
            return
        result = []
        for i in re.finditer(b'\x53\xEF', data):
            result.append(i.start() - 1080)
        offset = 0
        for i in result:
            if data[i] == 0:
                offset = i
                break
        if offset > 0:
            with open(output_file, 'wb') as o, open(input_file, 'rb') as f:
                data = f.seek(offset)
                data = f.read(15360)
                if data:
                    devnull = o.write(data)
        try:
            os.remove(input_file)
            os.rename(output_file, input_file)
        except:
            pass

    def MONSTER(self, target, output_dir):
        output_dir = Path(output_dir)
        if output_dir.is_symlink() or not output_dir.is_dir():
            raise ImageExtractionError(f'提取目录无效: {output_dir}')
        self.BASE_DIR = os.path.realpath(os.path.dirname(target)) + os.sep
        self.EXTRACT_DIR = str(output_dir.resolve()) + os.sep
        self.OUTPUT_IMAGE_FILE = self.BASE_DIR + os.path.basename(target)
        self.FileName = self.__file_name(os.path.basename(target))
        image_type = self.GetImageType(target)
        if image_type == 'simg':
            self.OUTPUT_IMAGE_FILE = self.Simg2Rimg(target)
        elif image_type != 'img':
            raise ImageExtractionError(f'无法识别 EXT4 镜像: {target}')

        with open(os.path.abspath(self.OUTPUT_IMAGE_FILE), 'rb') as stream:
            moto = re.search(b'MOTO', stream.read(500000))
        if moto:
            self.FIX_MOTO(os.path.abspath(self.OUTPUT_IMAGE_FILE))
        self.EXT4_EXTRACTOR()
        return True

    def LEMON(self, target):
        from pys import gettype
        if not os.path.exists(target):
            return 0
        target_type = gettype.gettype(target)
        if target_type == 'sparse':
            return self.__ImgSizeFromSparseFile(target)
        else:
            return os.path.getsize(target)

    def APPLE(self, target):
        target_type = self.GetImageType(target)
        if target_type == 'simg':
            return self.Simg2Rimg(target)

    def Simg2Rimg(self, target):
        """Convert Android sparse data while preserving RAW/FILL/DONT_CARE chunks."""
        def read_exact(stream, size, description):
            data = stream.read(size)
            if len(data) != size:
                raise ValueError(f'稀疏镜像{description}被截断')
            return data

        with open(target, 'rb') as img_file:
            if self.sign_offset > 0:
                img_file.seek(self.sign_offset, 0)
            header = EXT4_IMAGE_HEADER(read_exact(img_file, EXT4_SPARSE_HEADER_LEN, '文件头'))
            if header.magic != SPARSE_HEADER_MAGIC:
                raise ValueError(f'不是有效的稀疏镜像: {target}')
            if header.chunk_header_size < EXT4_CHUNK_HEADER_SIZE:
                raise ValueError(f'稀疏镜像 chunk header 无效: {target}')
            if header.file_header_size > EXT4_SPARSE_HEADER_LEN:
                read_exact(img_file, header.file_header_size - EXT4_SPARSE_HEADER_LEN, '扩展文件头')

            unsparse_file = target.replace('.img', '.unsparse.img')
            with open(unsparse_file, 'wb') as raw_img_file:
                for _ in range(header.total_chunks):
                    chunk_header = EXT4_CHUNK_HEADER(
                        read_exact(img_file, EXT4_CHUNK_HEADER_SIZE, 'chunk header')
                    )
                    if header.chunk_header_size > EXT4_CHUNK_HEADER_SIZE:
                        read_exact(
                            img_file,
                            header.chunk_header_size - EXT4_CHUNK_HEADER_SIZE,
                            '扩展 chunk header',
                        )
                    chunk_data_size = chunk_header.total_size - header.chunk_header_size
                    output_size = chunk_header.chunk_size * header.block_size
                    if chunk_data_size < 0:
                        raise ValueError(f'稀疏镜像 chunk 大小无效: {target}')

                    if chunk_header.type == 0xCAC1:  # RAW
                        if chunk_data_size != output_size:
                            raise ValueError(f'稀疏镜像 RAW chunk 大小无效: {target}')
                        remaining = output_size
                        while remaining:
                            data = read_exact(img_file, min(1024 * 1024, remaining), 'RAW 数据')
                            raw_img_file.write(data)
                            remaining -= len(data)
                    elif chunk_header.type == 0xCAC2:  # FILL
                        if chunk_data_size != 4:
                            raise ValueError(f'稀疏镜像 FILL chunk 大小无效: {target}')
                        fill = read_exact(img_file, 4, 'FILL 数据')
                        if output_size % len(fill):
                            raise ValueError(f'稀疏镜像 FILL 输出大小无效: {target}')
                        pattern = fill * (min(1024 * 1024, output_size) // len(fill))
                        remaining = output_size
                        while remaining:
                            data = pattern[:min(len(pattern), remaining)]
                            raw_img_file.write(data)
                            remaining -= len(data)
                    elif chunk_header.type == 0xCAC3:  # DONT_CARE
                        if chunk_data_size:
                            read_exact(img_file, chunk_data_size, 'DONT_CARE 数据')
                        raw_img_file.seek(output_size, 1)
                    elif chunk_header.type == 0xCAC4:  # CRC32
                        if output_size:
                            raise ValueError(f'稀疏镜像 CRC chunk 输出大小无效: {target}')
                        read_exact(img_file, chunk_data_size, 'CRC 数据')
                    else:
                        raise ValueError(f'不支持的稀疏镜像 chunk 类型: {chunk_header.type:#x}')
                raw_img_file.truncate(raw_img_file.tell())
            return unsparse_file

    def EXT4_EXTRACTOR(self):
        output_root = Path(self.EXTRACT_DIR).resolve()
        config_dir = output_root.parent / 'config'
        if output_root.is_symlink() or not output_root.is_dir():
            raise ImageExtractionError(f'EXT4 输出目录无效: {output_root}')
        if config_dir.is_symlink():
            raise ImageExtractionError(f'EXT4 metadata 目录无效: {config_dir}')
        config_dir.mkdir(parents=True, exist_ok=True)

        contexts_path = config_dir / f'{self.FileName}_contexts.txt'
        fsconfig_path = config_dir / f'{self.FileName}_fsconfig.txt'
        info_path = config_dir / f'{self.FileName}_info.txt'
        space_path = config_dir / f'{self.FileName}_space.txt'
        partition_size = os.path.getsize(self.OUTPUT_IMAGE_FILE)
        with open(self.OUTPUT_IMAGE_FILE, 'rb') as filesystem:
            filesystem.seek(1024)
            superblock = filesystem.read(1024)
        if len(superblock) != 1024:
            raise ImageExtractionError(f'EXT4 superblock 被截断: {self.OUTPUT_IMAGE_FILE}')
        inode_count = struct.unpack_from('<L', superblock, 0)[0]
        block_size = 1024 << struct.unpack_from('<L', superblock, 24)[0]
        per_group = struct.unpack_from('<L', superblock, 32)[0]
        label = bytes(superblock[120:136]).rstrip(b'\x00').decode('utf-8', 'replace')
        manifest = {
            'a': inode_count,
            'b': block_size,
            'c': per_group,
            'd': label,
            'e': 'ext4',
            's': partition_size,
        }

        seen_targets = set()

        def output_path(components):
            if not components or any(
                not component or component in {'.', '..'} or '/' in component or '\\' in component
                or any(character.isspace() for character in component) or '"' in component
                for component in components
            ):
                raise ImageExtractionError(f'EXT4 包含无法安全表示的路径: {components!r}')
            target = output_root.joinpath(*components)
            try:
                target.relative_to(output_root)
            except ValueError as error:
                raise ImageExtractionError(f'EXT4 路径越界: {components!r}') from error
            if target in seen_targets:
                raise ImageExtractionError(f'EXT4 路径冲突: {target}')
            if target.parent.is_symlink() or not target.parent.is_dir():
                raise ImageExtractionError(f'EXT4 父目录无效: {target.parent}')
            seen_targets.add(target)
            return target

        def read_link(inode, volume):
            reader = inode.open_read()
            try:
                data = reader.read(65536)
                if reader.read(1):
                    raise ImageExtractionError('EXT4 符号链接目标过长')
            finally:
                close_reader = getattr(reader, 'close', None)
                if close_reader:
                    close_reader()
            try:
                return data.decode('utf-8')
            except UnicodeDecodeError:
                if len(data) > 8:
                    raise ImageExtractionError('EXT4 符号链接目标无法解码')
                block = int.from_bytes(data, 'little')
                return volume.read(block * volume.block_size, inode.inode.i_size).decode('utf-8')

        def write_file(inode, target):
            reader = inode.open_read()
            try:
                with open(target, 'xb') as out:
                    while True:
                        chunk = reader.read(1024 * 1024)
                        if not chunk:
                            break
                        if out.write(chunk) != len(chunk):
                            raise ImageExtractionError(f'EXT4 文件写入不完整: {target}')
            except OSError as error:
                raise ImageExtractionError(f'EXT4 文件写入失败: {target}: {error}') from error
            finally:
                close_reader = getattr(reader, 'close', None)
                if close_reader:
                    close_reader()

        def scan_dir(root_inode, components=()):
            for entry_name, entry_inode_idx, entry_type in root_inode.open_dir():
                # ext4 preallocates empty directory slots (inode=0), commonly
                # inside lost+found. They are not real filesystem entries.
                if not is_valid_ext4_directory_entry(entry_name, entry_inode_idx):
                    continue
                entry_inode = root_inode.volume.get_inode(entry_inode_idx, entry_type)
                entry_components = (*components, entry_name)
                target = output_path(entry_components)
                mode = self.__getperm(entry_inode.mode_str)
                if mode is None:
                    raise ImageExtractionError(f'EXT4 文件权限无效: {entry_name!r}')
                uid = entry_inode.inode.i_uid
                gid = entry_inode.inode.i_gid
                relative_path = '/'.join(entry_components)
                fs_path = f'{self.FileName}/{relative_path}'
                cap = ''
                link_target = ''
                for attribute, value in entry_inode.xattrs():
                    if attribute == 'security.selinux':
                        escaped = fs_path
                        for character in '\\^$.|?*+(){}[]':
                            escaped = escaped.replace(character, '\\' + character)
                        self.contexts.append(f'/{escaped} {value.decode("utf-8").rstrip(chr(0))}')
                    elif attribute == 'security.capability':
                        values = struct.unpack('<5I', value)
                        if values[1] > 65535:
                            capability = hex(int(f'{values[3]:04x}{values[1]:04x}', 16))
                        else:
                            capability = hex(int(f'{values[3]:04x}{values[2]:04x}{values[1]:04x}', 16))
                        cap = f' capabilities={capability}'

                if entry_inode.is_dir:
                    try:
                        target.mkdir()
                    except OSError as error:
                        raise ImageExtractionError(f'EXT4 目录创建失败: {target}: {error}') from error
                    if os.geteuid() == 0:
                        os.chmod(target, int(mode, 8))
                        os.chown(target, uid, gid)
                    self.fsconfig.append(f'{fs_path} {uid} {gid} {mode}{cap}')
                    scan_dir(entry_inode, entry_components)
                elif entry_inode.is_file:
                    write_file(entry_inode, target)
                    if os.geteuid() == 0:
                        os.chmod(target, int(mode, 8))
                        os.chown(target, uid, gid)
                    self.fsconfig.append(f'{fs_path} {uid} {gid} {mode}{cap}')
                elif entry_inode.is_symlink:
                    link_target = read_link(entry_inode, root_inode.volume)
                    try:
                        os.symlink(link_target, target)
                    except OSError as error:
                        raise ImageExtractionError(f'EXT4 符号链接创建失败: {target}: {error}') from error
                    self.fsconfig.append(f'{fs_path} {uid} {gid} {mode}{cap} {link_target}')
                else:
                    raise ImageExtractionError(f'EXT4 包含不支持的文件类型: {entry_name!r}')

        with open(self.OUTPUT_IMAGE_FILE, 'rb') as image_file:
            scan_dir(ext4.Volume(image_file).root)

        partition_name = self.FileName
        self.fsconfig.insert(0, '/ 0 2000 0755' if partition_name == 'vendor' else '/ 0 0 0755')
        self.fsconfig.insert(1, f'{partition_name} 0 2000 0755' if partition_name == 'vendor' else '/lost+found 0 0 0700')
        self.fsconfig.insert(2 if partition_name == 'system' else 1, f'{partition_name} 0 0 0755')
        self.__appendf('\n'.join(self.fsconfig), fsconfig_path)
        self.__appendf('\n'.join(self.space), space_path)
        with codecs.open(info_path, 'w', 'utf-8') as stream:
            json.dump(manifest, stream, indent=4)
        if self.contexts:
            self.contexts.sort()
            root_context = None
            for context in self.contexts:
                fields = context.split(maxsplit=1)
                if len(fields) == 2 and 'lost..found' in fields[0]:
                    root_context = fields[1]
                    break
            if root_context:
                self.contexts.insert(0, f'/ {root_context}')
                self.contexts.insert(1, f'/{partition_name}(/.*)? {root_context}')
                self.contexts.insert(2, f'/{partition_name} {root_context}')
                self.contexts.insert(3, f'/{partition_name}/lost+\\found {root_context}')
        self.__appendf('\n'.join(self.contexts), contexts_path)
        return True
