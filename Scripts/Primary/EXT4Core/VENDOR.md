# EXT4Core

本目录是第三方库 **python-ext4** 的 vendored 副本，仅服务于 A.R.T 的 EXT4 镜像分解。

## 原项目

- **名称**：python-ext4 (PyPI 包名 `ext4`)
- **作者**：Nathaniel van Diepen (Albert "Eeems" Wiersing)
- **仓库**：https://github.com/Eeems/python-ext4
- **PyPI**：https://pypi.org/project/ext4/
- **协议**：MIT License（见下方全文）

## Vendored 信息

- **引入 A.R.T 的时间**：2026-10-02
- **引入提交**：`30f8375` — "ext4: vendor Eeems python-ext4 v1.4 parser, keep Android fsconfig layer"
- **对应上游版本**：v1.4 系列（commit 消息标注 "v1.4 parser"）
- **当前对应提交**：A.R.T 内部维护，未跟踪上游后续 tag；与上游 1.4.1 的差异仅为代码风格（`_fields_` 元组重构、`Self` 类型注解、pyright 注释），分解（读取）层面功能等价、零逻辑差异

## 适配说明

A.R.T 在 vendor 时做了以下适配，使其不依赖 pip 安装即可运行：

- 将上游依赖 `cachetools` 一并 vendored 到 `_vendored/cachetools/`
- 移除了对 `crcmod` / `typing_extensions` 的外部依赖
- `_compat.py` 内置了 `PeekableStream` 等兼容实现
- `Volume` 支持 `ignore_attr_name_index` / `ignore_checksum`，用于容忍 vendor 镜像中非标准的 xattr name index

## MIT License

```
MIT License

Copyright (c) 2024 Nathaniel van Diepen

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
