# **Android ROM Toolkit**

#### **介绍**

一个Android ROM工具，简称**A.R.T**
基于[D.N.A3](https://github.com/ColdWindScholar/D.N.A3.git)源码的**Linux CLI**工具

#### **运行平台**

- Linux环境下`X86_64`的指令集的设备
- 为了体验，你的设备需要满足**glibc\>2.35  Linux kernel\>5.15 LTS**
- 推荐使用 Debian 12或以上，Ubuntu 22.04 LTS或以上

#### **注意事项**

- **非必要请不要授予工具SU ! ! !**

- **请勿删除【工程目录/configs文件夹】，打包时所需的文件信息都在此处**

- 工具涉及的路径 **不要有除英文外的其他语言** 或 **特殊符号**

#### **工具预览**

[工具主页](https://github.com/ELF-RC/A.R.T/blob/master/Picture/image1.png)
[设置](https://github.com/ELF-RC/A.R.T/blob/master/Picture/image2.png)

#### **工程目录结构**

```text
DNA_工程名/
├── INPUT/                 # 原始输入区：放置 img、payload.bin、new.dat、br、win 等文件
├── OUT/                   # 最终输出区：合成的 img、new.dat、br、super.img、修补后的 boot 镜像
└── WORKSPACE/             # 可编辑工作区
    ├── config/            # 分区 fsconfig、SELinux contexts、镜像信息等合成 metadata
    ├── system/
    ├── vendor/
    └── 其他分区目录/
```

- `INPUT` 是只读输入区：工具不会主动删除、改名或覆盖其中的文件；所有中间文件写入 `WORKSPACE`
- 分区文件树与 `config` 均位于 `WORKSPACE/`，请勿手动删除 `WORKSPACE/config/` 中需要回包的 metadata
- 最终合成产物输出到 `OUT/`

#### **构建**

1.安装python3,pip  

2.pip install -r requirements.txt --break-system-packages (这里更推荐使用虚拟环境)  

3.执行./build.py构建  


#### **反馈**

请直接提issue
