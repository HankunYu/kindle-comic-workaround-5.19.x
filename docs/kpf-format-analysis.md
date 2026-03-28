# KPF (Kindle Publishing Format) 格式分析

> 基于对真实 KPF 文件（JOJO的奇妙冒险卷01，172页漫画）的完整逆向分析。
> 使用 kfxlib（Calibre KFX Output 插件）的 Ion 解码器解析所有 fragment。

---

## 一、KPF 文件总体结构

KPF 是一个 ZIP 包，包含：

```
book.kcb                    # JSON 配置文件（书籍元信息、工具版本等）
book_0.jpg ~ book_N.jpg     # 封面/预览用缩略图
resources/book.kdf          # SQLite 数据库（核心数据，Ion 编码的 fragments）
resources/res/rsrcXXX       # 图片资源的原始二进制数据
resources/ManifestFile      # 清单文件
action.log                  # 操作日志
```

### book.kcb (JSON 配置文件)

```json
{
  "book_state": {
    "book_fl_type": 1,
    "book_input_type": 4,
    "book_reading_direction": 2,
    "book_reading_option": 1,
    "book_target_type": 3,
    "book_virtual_panelmovement": 0
  },
  "content_hash": {
    "action.log": "ef37c16b13731beac77a630b9168a4c3",
    "book_1.jpg": "13630e1d60222a0b4a209811ad2ab4c2",
    ...  // MD5 hash for each file
  },
  "metadata": {
    "book_path": "resources",
    "edited_tool_versions": ["1.110.0.0"],
    "format": "yj",
    "global_styling": true,
    "id": "d29bc578-d979-4ebc-9270-99967f574ef8",
    "platform": "mac",
    "tool_name": "KC",
    "tool_version": "1.110.0.0"
  }
}
```

**关键字段含义**:
- `book_fl_type: 1` — 固定布局类型
- `book_input_type: 4` — 图片输入
- `book_reading_direction: 2` — 从右到左阅读
- `book_reading_option: 1` — 阅读选项
- `book_target_type: 3` — 目标类型
- `tool_name: "KC"` — Kindle Create
- `format: "yj"` — Kindle 内部格式名称

### ManifestFile

```
AmazonYJManifest
digital_content_manifest::{
  version:1,
  storage_type:"localSqlLiteDB",
  digital_content_name:"book.kdf"
}
```

### action.log

记录 Kindle Create 操作日志（可为简单模板内容）。

### book_N.jpg 预览图

ZIP 根目录包含 `book_0.jpg` ~ `book_N.jpg`，是每页的 JPEG 预览/缩略图。
命名从 `book_1.jpg` 开始，按页序递增。

### book.kdf SQLite 数据库结构

```sql
CREATE TABLE capabilities(key char(20), version smallint,
    PRIMARY KEY (key, version)) WITHOUT ROWID;

CREATE TABLE fragments(id char(40), payload_type char(10), payload_value blob,
    PRIMARY KEY (id));

CREATE TABLE fragment_properties(id char(40), key char(40), value char(40),
    PRIMARY KEY (id, key, value)) WITHOUT ROWID;

CREATE TABLE gc_fragment_properties(id varchar(40), key varchar(40), value varchar(40),
    PRIMARY KEY (id, key, value)) WITHOUT ROWID;

CREATE TABLE gc_reachable(id varchar(40),
    PRIMARY KEY (id)) WITHOUT ROWID;
```

**capabilities 表**: 仅有一条记录 `db.schema = 1`。

**fragments 表**: 每条记录是一个 fragment，payload_type 为 `blob`（Ion 二进制编码）或 `path`（资源路径字符串）。

**fragment_properties 表**: 记录每个 fragment 的 `element_type` 和 `child` 关系。

### 数据库指纹保护（Fingerprint Wrapper）

原始 book.kdf 文件被指纹记录包裹，需要先去指纹才能作为标准 SQLite 打开。

**指纹结构**:
- 偏移 1024 字节处插入第一条指纹记录
- 每条指纹记录 1024 字节，以签名 `FA 50 0A 5F` 开头
- 之后每隔 1024 × 1024 = 1MB 数据插入一条指纹记录
- 去指纹时只需删除这些 1024 字节的记录即可还原原始 SQLite

```python
# Fingerprint removal algorithm
FINGERPRINT_OFFSET = 1024
FINGERPRINT_RECORD_LEN = 1024
DATA_RECORD_LEN = 1024
DATA_RECORD_COUNT = 1024
FINGERPRINT_SIGNATURE = b"\xfa\x50\x0a\x5f"

def remove_fingerprints(data):
    """Remove fingerprint records from KDF data."""
    offset = FINGERPRINT_OFFSET
    while len(data) >= offset + FINGERPRINT_RECORD_LEN:
        if data[offset:offset + 4] != FINGERPRINT_SIGNATURE:
            break
        data = data[:offset] + data[offset + FINGERPRINT_RECORD_LEN:]
        offset += DATA_RECORD_LEN * DATA_RECORD_COUNT  # next after 1MB
    return data
```

**生成 KPF 时**: 需要在写入完成的 SQLite 文件上反向执行此操作——在对应偏移处插入指纹记录。

---

## 二、Ion 二进制编码

所有 blob 类型的 fragment 使用 Amazon Ion 二进制格式编码（基于 https://amzn.github.io/ion-docs/）。

### Ion 签名

每个 blob payload 以 4 字节 Ion BVM (Binary Version Marker) 开头：`E0 01 00 EA`

### 符号表体系

Ion 使用符号表将频繁出现的字符串映射为整数 ID，减少空间占用。

**系统符号表** ($ion, $1-$9)：
| ID | 符号 |
|----|------|
| $1 | $ion |
| $2 | $ion_1_0 |
| $3 | $ion_symbol_table |
| $4 | name |
| $5 | version |
| $6 | imports |
| $7 | symbols |
| $8 | max_id |
| $9 | $ion_shared_symbol_table |

**YJ_symbols 共享符号表** (version=10, $10-$851, 共842个符号)：
Amazon 的 Kindle 格式使用名为 `YJ_symbols` 的共享符号表，所有字段名都以 `$N` 形式引用。

**本地符号表** ($843+, 此文件定义了3个额外符号)：
| ID | 符号 |
|----|------|
| $843 | yj.authoring.source_file_name |
| $844 | yj.authoring.original_resource |
| $845 | yj.authoring.preserved_original_resource |

### $ion_symbol_table Fragment

```ion
$ion_symbol_table::{
    max_id: 854,
    imports: [{name: "YJ_symbols", version: 10, max_id: 842}],
    symbols: [
        "yj.authoring.source_file_name",
        "yj.authoring.original_resource",
        "yj.authoring.preserved_original_resource"
    ]
}
```

---

## 三、Fragment 类型总览

本 KPF 文件共包含 **1455 个 fragments**，分为以下类型：

| element_type | 注解符号 | 数量 | 说明 |
|---|---|---|---|
| structure | $608 | 344 | 页面内容结构节点 |
| auxiliary_data | $597 | 174 | 辅助数据（资源元信息等） |
| external_resource | $164 | 173 | 外部资源引用 |
| bcRawMedia | (path) | 173 | 原始媒体资源路径 |
| section | $260 | 172 | 页面/章节 |
| section_position_id_map | $609 | 172 | 章节位置-ID映射 |
| storyline | $259 | 172 | 故事线/内容流 |
| yj.eidhash_eid_section_map | $610 | 67 | EID哈希桶 |
| $ion_symbol_table | - | 1 | 符号表 |
| book_metadata | $490 | 1 | 书籍元数据 |
| book_navigation | - | 1 | 导航（本例为空） |
| content_features | $585 | 1 | 内容特性声明 |
| document_data | $538 | 1 | 文档全局数据 |
| max_id | - | 1 | 最大符号ID |
| metadata | $258 | 1 | 阅读顺序元数据 |
| yj.section_pid_count_map | $611 | 1 | 章节PID计数映射 |

---

## 四、关键 Symbol 含义映射

通过分析 kfxlib 源代码和实际数据，以下是核心 `$N` 符号的含义：

### 注解符号（Fragment Type）

| 符号 | 含义 | 说明 |
|------|------|------|
| $258 | metadata | 阅读顺序/书籍元数据 |
| $259 | storyline | 故事线 |
| $260 | section | 章节/页面 |
| $490 | book_metadata | 书籍元数据（分类元信息） |
| $538 | document_data | 文档全局配置 |
| $585 | content_features | 内容特性 |
| $597 | auxiliary_data | 辅助数据 |
| $608 | structure | 内容结构节点 |
| $609 | section_position_id_map | 章节位置映射 |
| $610 | yj.eidhash_eid_section_map | EID哈希映射 |
| $611 | yj.section_pid_count_map | 章节PID计数 |
| $164 | external_resource | 外部资源 |

### 结构字段符号

| 符号 | 含义 | 上下文 |
|------|------|--------|
| $56 | width | structure 节点宽度 |
| $57 | height | structure 节点高度 |
| $66 | page_width | section 页面宽度 |
| $67 | page_height | section 页面高度 |
| $140 | page_template_type | 页面模板类型 |
| $141 | section_content | section 的内容描述列表 |
| $144 | count | PID 计数 |
| $146 | children | 子节点引用列表 |
| $156 | layout_type | 布局类型 |
| $159 | node_type | 节点类型 |
| $161 | format | 资源格式 |
| $165 | location | 资源位置(rsrc ID) |
| $169 | reading_orders | 阅读顺序列表 |
| $170 | sections | 章节引用列表 |
| $174 | section_id | 章节ID |
| $175 | resource_id | 资源ID |
| $176 | storyline_id | 故事线ID |
| $178 | reading_order_name | 阅读顺序名称 |
| $181 | position_map | 位置映射列表 |
| $183 | fit_type | 图片适应方式 |
| $185 | eid | 元素ID |
| $192 | layout | 布局模式 |
| $258 | metadata_list | 元数据键值列表 |
| $307 | value | 元数据值 |
| $422 | image_width | 图片原始宽度 |
| $423 | image_height | 图片原始高度 |
| $491 | categories | 元数据分类 |
| $492 | key | 元数据键名 |
| $495 | category_name | 分类名称 |
| $546 | position_type | 位置类型 |
| $560 | page_progression_direction | 翻页方向 |
| $581 | binding | 装订方向 |
| $586 | namespace | 特性命名空间 |
| $587 | major_version | 主版本号 |
| $588 | minor_version | 次版本号 |
| $589 | properties | 特性属性 |
| $590 | features_list | 特性列表 |
| $597 | aux_data_ref | 辅助数据引用 |
| $598 | eid / self_ref | 元素ID/自引用 |
| $602 | bucket_index | 哈希桶索引 |
| $613 | resource_list_ref | 资源列表引用 |

### 枚举值符号

| 符号 | 含义 | 上下文 |
|------|------|--------|
| $270 | container | 节点类型: 容器 |
| $271 | leaf | 节点类型: 叶子节点 |
| $284 | png | 资源格式: PNG |
| $285 | jpg | 资源格式: JPEG |
| $320 | fixed | 页面模板类型: 固定布局 |
| $323 | block | 布局类型: 块 |
| $324 | fit_both | 图片适应: 双向适应 |
| $326 | fixed_layout | 布局类型: 固定布局 |
| $351 | default | 阅读顺序: 默认 |
| $375 | fixed_layout_mode | 文档布局模式 |
| $377 | absolute | 位置类型: 绝对定位 |
| $441 | right_to_left | 装订方向: 从右到左 |
| $557 | right_to_left | 翻页方向: 从右到左 |

---

## 五、每页（Page/Section）的完整 Fragment 链

对于固定布局漫画，每一页由以下 fragment 链组成：

```
section (c*)
  ├── section_content ($141) → 包含 structure 描述
  │     └── structure inline {eid: t*, storyline: l*, width, height, layout, template, node_type}
  ├── section_position_id_map (c*-spm)
  │     └── 位置索引 → eid 映射
  ├── storyline (l*)
  │     └── children ($146) → [structure i*]
  ├── structure (i*) — 容器节点
  │     └── children ($146) → [structure i*]
  ├── structure (i*) — 叶子节点（图片引用）
  │     └── resource ($175) → external_resource (e*)
  ├── external_resource (e*)
  │     ├── location ($165) → "rsrcXX" (bcRawMedia path)
  │     ├── format ($161) → $285 (jpg) / $284 (png)
  │     ├── aux_data ($597) → auxiliary_data (d*)
  │     ├── width ($422), height ($423)
  │     └── source_file_name
  ├── bcRawMedia (rsrc*)
  │     └── path: "res/rsrcXX" → 实际图片文件
  └── auxiliary_data (d*)
        └── metadata: type=resource, resource_stream, size, modified_time, location
```

### 具体示例：第4页 (cW)

```
section cW (annotation: $260)
├── $174: cW (section ID)
└── $141 (content list):
    └── $608 structure:
        ├── $598: tX (structure EID)
        ├── $176: lY (storyline ref)
        ├── $66: 960 (page width)
        ├── $67: 1216 (page height)
        ├── $156: $326 (fixed_layout)
        ├── $140: $320 (fixed template)
        └── $159: $270 (container node)

storyline lY (annotation: $259)
├── $176: lY (self ID)
└── $146: [i13] (children)

structure i13 (annotation: $608) — container node
├── $598: i13 (EID)
├── $56: 960, $57: 1216 (dimensions)
├── $546: $377 (absolute position)
├── $156: $323 (block layout)
├── $159: $270 (container)
└── $146: [i14]

structure i14 (annotation: $608) — leaf node (image)
├── $598: i14 (EID)
├── $56: 960, $57: 1216 (dimensions)
├── $175: e12 (resource reference)
├── $546: $377 (absolute)
├── $159: $271 (leaf)
└── $183: $324 (fit_both)

external_resource e12 (annotation: $164)
├── source_file_name: "0004.jpg"
├── $161: $285 (jpg format)
├── $165: "rsrc11" (resource location)
├── $597: d10 (aux data ref)
├── $422: 960.0, $423: 1216.0 (original image dimensions)
└── $175: e12 (self ref)

bcRawMedia rsrc11 → path: "res/rsrc11"

auxiliary_data d10 (annotation: $597)
├── $598: d10 (self ref)
└── $258 (metadata list):
    ├── {$492: "type", $307: "resource"}
    ├── {$492: "resource_stream", $307: "rsrc11"}
    ├── {$492: "size", $307: "293227"}
    ├── {$492: "modified_time", $307: "1774702335"}
    └── {$492: "location", $307: "/path/to/0004.jpg"}
```

### section_position_id_map (cW-spm)

每个 section 对应一个位置映射，记录 EID 在 section 内的位置索引：

```
$609::{
    $174: cW,           # section ID
    $181: [             # position map
        [1, tX],        # position 1 → structure EID (from section $141)
        [2, i13],       # position 2 → container structure
        [3, i14]        # position 3 → leaf structure (image)
    ]
}
```

对于漫画，每页固定 3 个位置（1个内联结构 + 1个容器 + 1个叶子）。

---

## 六、PNG 资源的特殊处理

当源图是 PNG 格式时，Kindle Create 会将其转换为 JPEG，但保留原始 PNG。
此时 external_resource 有两个变体：

```
external_resource e1GB (原始 PNG)
├── $161: $284 (png)
├── $165: "rsrc1G9"      # PNG 资源
├── $422: 1800, $423: 2400

external_resource e1GE (JPEG 转换版)
├── $161: $285 (jpg)
├── $165: "rsrc1GD"      # JPEG 资源
├── yj.authoring.original_resource: e1GB (指向原始资源)
├── yj.authoring.preserved_original_resource: {
│       $161: $284, $165: "rsrc1G9",
│       $422: 1800, $423: 2400
│   }
├── $422: 1800.0, $423: 2400.0
```

auxiliary_data 也有两条：
- d1G8: 原始 PNG 资源元数据 + JPEG 转换引用
- d1GC: JPEG 转换后的资源元数据

---

## 七、全局 Fragment 详细结构

### 7.1 document_data ($538)

文档全局配置，包含页面排列信息：

```
$538::{
    $16: 16.0,                          # 格式版本
    $560: $557,                         # 翻页方向 (right_to_left)
    max_id: 1553,                       # 最大EID值
    $192: $375,                         # 布局模式 (fixed_layout)
    $581: $441,                         # 装订方向 (right_to_left)
    $597: {                             # 辅助数据引用
        $613: $598::"d5"               # 指向资源列表 auxiliary_data
    },
    $169: [{                            # 阅读顺序
        $178: $351,                     # 名称: default
        $170: [$598::"c0", $598::"cA", ..., $598::"c1G4"]  # 172个section引用
    }]
}
```

### 7.2 metadata ($258)

阅读顺序元数据（与 document_data 中的 $169 内容相同）：

```
$258::{
    $169: [{
        $178: $351,                     # 阅读顺序名: default
        $170: [$598::"c0", $598::"cA", ..., $598::"c1G4"]
    }]
}
```

### 7.3 book_metadata ($490)

分类元数据，包含 4 个分类：

```
$490::{
    $491: [
        {
            $495: "kindle_capability_metadata",
            $258: [
                {$492: "yj_publisher_panels", $307: 1},
                {$492: "yj_fixed_layout", $307: 1}
            ]
        },
        {
            $495: "kindle_title_metadata",
            $258: [
                {$492: "book_id", $307: "P_1Hjgf1TqepBFgVrdAL0Q0"},
                {$492: "language", $307: "en-US"}
            ]
        },
        {
            $495: "kindle_ebook_metadata",
            $258: [
                {$492: "selection", $307: "enabled"}
            ]
        },
        {
            $495: "kindle_audit_metadata",
            $258: [
                {$492: "file_creator", $307: "KC"},
                {$492: "creator_version", $307: "1.110.0.0"}
            ]
        }
    ]
}
```

### 7.4 content_features ($585)

声明本书使用的 Kindle 特性：

```
$585::{
    $598: $585,                         # self ref
    $590: [
        {
            $586: "com.amazon.yjconversion",
            $492: "yj_non_pdf_fixed_layout",
            $589: {version: {$587: 2, $588: 0}}
        },
        {
            $586: "com.amazon.yjconversion",
            $492: "yj_publisher_panels",
            $589: {version: {$587: 2, $588: 0}}
        }
    ]
}
```

### 7.5 max_id Fragment

简单整数值，记录当前符号表的最大 ID：`854`

### 7.6 book_navigation

在本漫画 KPF 中为**空**（仅包含 Ion 签名，无实际数据）。

---

## 八、辅助数据与资源列表

### 8.1 资源列表 auxiliary_data (d5)

被 document_data 引用（通过 $613），列出所有资源的 auxiliary_data 引用：

```
$597::{
    $598: $598::"d5",
    $258: [
        {$492: "auxData_resource_list", $307: [$598::"d4", $598::"dE", ...]}
    ]
}
```

### 8.2 单个资源的 auxiliary_data (d4, dE, ...)

```
$597::{
    $598: $598::"d4",
    $258: [
        {$492: "type", $307: "resource"},
        {$492: "resource_stream", $307: "rsrc6"},
        {$492: "size", $307: "238659"},
        {$492: "modified_time", $307: "1774702335"},
        {$492: "location", $307: "/path/to/0001.jpg"}
    ]
}
```

---

## 九、EID 哈希映射

### 9.1 yj.eidhash_eid_section_map (eidbucket_0 ~ eidbucket_66)

67 个哈希桶，将 EID 映射到所属的 section：

```
$610::{
    $602: 0,                            # 桶索引
    $181: [
        {$185: $598::"i1B0", $174: $598::"c1AT"},  # EID i1B0 在 section c1AT 中
        {$185: $598::"c1A7", $174: $598::"c1A7"},  # section 自身也作为 EID
        ...
    ]
}
```

### 9.2 yj.section_pid_count_map

记录每个 section 中的位置 ID (PID) 数量：

```
$611::{
    $181: [
        {$174: $598::"c1R", $144: 3},  # section c1R 有 3 个 PID
        {$174: $598::"cA", $144: 3},
        ...
    ]
}
```

对于漫画，每个 section 固定有 **3 个 PID**。

---

## 十、fragment_properties 关系

每个 fragment 在 `fragment_properties` 表中记录：

### element_type 属性
每个 fragment 都有 element_type：
- section fragments → `element_type = "section"`
- section_position_id_map → `element_type = "section_position_id_map"`
- storyline → `element_type = "storyline"`
- structure → `element_type = "structure"`
- external_resource → `element_type = "external_resource"`
- auxiliary_data → `element_type = "auxiliary_data"`
- bcRawMedia → `element_type = "bcRawMedia"`
- 全局类型 → 直接使用类型名（如 `"book_metadata"`）

### child 属性
记录 fragment 间的父子关系，例如：
```
section c0:
  child → c0-ad          # 辅助数据（虚拟，不存在为实际 fragment）
  child → l2             # storyline

storyline l2:
  child → i8             # structure
  child → l2             # 自引用

structure i8:
  child → i9             # 子 structure

structure i9:
  child → e7             # external_resource

external_resource e7:
  child → d4             # auxiliary_data
  child → rsrc6          # bcRawMedia
```

---

## 十一、ID 编码规则

Fragment ID 使用**自定义 base-32 编码**，字符集为：
```
0123456789ABCDEFGHJKMNPRSTUVWXYZ
```
（排除了 I, L, O, Q 这4个容易混淆的字母）

各类型 fragment 使用不同的前缀：
| 前缀 | 类型 | 示例 |
|------|------|------|
| c | section | c0, cA, cK, c1G4 |
| t | structure (inline in section) | t1, tB, tX |
| l | storyline | l2, lC, lY |
| i | structure (standalone) | i8, i9, iH |
| e | external_resource | e7, eG, eT |
| d | auxiliary_data | d4, d5, dE |
| rsrc | bcRawMedia | rsrc6, rsrcF |

section_position_id_map 使用 `{section_id}-spm` 格式（如 `c0-spm`）。

---

## 十二、创建最小有效 KPF 的必要条件

基于以上分析，创建固定布局漫画 KPF 需要的最小 fragment 集合：

### 必须的全局 Fragments

1. **$ion_symbol_table** — 符号表（导入 YJ_symbols v10）
2. **max_id** — 最大符号 ID 值
3. **book_metadata** ($490) — 分类元数据
   - kindle_capability_metadata: yj_fixed_layout=1, yj_publisher_panels=1
   - kindle_title_metadata: book_id, language
   - kindle_audit_metadata: file_creator="KC", creator_version
4. **document_data** ($538) — 文档全局配置
   - 格式版本、布局模式、翻页方向、装订方向
   - 阅读顺序（section 列表）
   - 资源列表 auxiliary_data 引用
5. **metadata** ($258) — 阅读顺序
6. **content_features** ($585) — 特性声明
7. **yj.section_pid_count_map** ($611)
8. **book_navigation** — 可为空

### 每页需要的 Fragments

对于每个图片页，需要创建 **8-9 个 fragments**：

| # | Fragment | element_type | 说明 |
|---|----------|-------------|------|
| 1 | section (c*) | section | 页面定义 |
| 2 | section_position_id_map (c*-spm) | section_position_id_map | 位置映射 |
| 3 | storyline (l*) | storyline | 故事线 |
| 4 | structure (i*) | structure | 容器节点 |
| 5 | structure (i*) | structure | 叶子节点（图片） |
| 6 | external_resource (e*) | external_resource | 资源引用 |
| 7 | bcRawMedia (rsrc*) | bcRawMedia | 资源路径 |
| 8 | auxiliary_data (d*) | auxiliary_data | 资源元数据 |

### 全局辅助 Fragments

| # | Fragment | 说明 |
|---|----------|------|
| 1 | auxiliary_data (d*) for resource list | 资源列表索引 |
| 2-N | eidbucket_* (67个桶) | EID 哈希映射 |

### capabilities 表

```sql
INSERT INTO capabilities VALUES ('db.schema', 1);
```

### fragment_properties 表

每个 fragment 需要对应的 element_type 和 child 关系记录。

### resources 目录

每个图片存储为 `res/rsrcXXX`（无扩展名的原始图片数据）。

---

## 十三、页面尺寸

本样本书中图片尺寸各异（不同扫描/处理结果），主要集中在：
- 728x1200 (39页)
- 760x1200 (31页)
- 960x1216 (26页)
- 736x1200 (22页)
- 960x1280 (13页)

每个 section 的 `$66`/`$67`（页面宽高）对应其引用图片的实际像素尺寸。
structure 节点的 `$56`/`$57` 也使用相同尺寸。

---

## 十四、Ion 二进制编码要点

### 值类型编码（type descriptor byte）

```
高4位 = 类型签名    低4位 = flag/length
0 = null/nop       1 = bool
2 = positive int    3 = negative int
4 = float          5 = decimal
6 = timestamp      7 = symbol (ID)
8 = string (UTF-8) 9 = clob
10 = blob          11 = list
12 = sexp          13 = struct
14 = annotation    15 = reserved
```

当 flag=14 时，后续有 VarUInt 表示实际长度。
当 flag=15 时，表示 null 值。

### VarUInt/VarInt 编码

- **VarUInt**: 每字节低7位数据，最高位=1表示结束
- **VarInt**: 首字节第6位为符号位，其余同 VarUInt

### 引用模式

KPF 中大量使用 `$598::` 注解来表示 fragment 引用：
```ion
$598::"c0"    // 引用 section c0
$598::"l2"    // 引用 storyline l2
```

此注解 `$598` 标记该字符串值是一个 EID (Element ID)，而非普通字符串。

---

## 十五、实现建议

### 生成 KPF 的关键步骤

1. **准备图片**: 读取所有图片，获取尺寸
2. **分配 ID**: 按 base-32 规则为每个 fragment 分配 ID
3. **创建符号表**: 导入 YJ_symbols v10 + 本地符号
4. **逐页创建 fragment 链**: section → spm → storyline → structures → resource → media
5. **创建全局 fragments**: document_data, metadata, book_metadata, content_features 等
6. **创建 eidbucket 映射**: 将所有 EID 哈希分桶
7. **Ion 编码所有 fragments**: 使用 IonBinary 序列化
8. **写入 SQLite**: 创建 fragments, fragment_properties, capabilities 表
9. **应用指纹包裹**: 对 SQLite 文件应用 DRMION 指纹
10. **打包 KPF ZIP**: 加入 book.kcb, book.kdf, 图片等

### 简化可能

对于纯图片漫画，许多结构是高度规律的：
- 每页的 structure 层级固定（1个容器 + 1个叶子）
- section_position_id_map 固定3个位置
- yj.section_pid_count_map 中每页固定3个 PID
- 所有 symbol 值都是预定义的枚举
- book_navigation 可为空

### EID 哈希桶算法（已验证 100% 匹配）

```python
def eid_hash_bucket(eid_string, num_buckets):
    """EID to bucket index, verified against real KPF data."""
    return sum(ord(c) for c in eid_string) % num_buckets
```

**哈希方法**: 简单字符 ASCII 码求和取模。
**桶数量**: 本样本为 67 桶（对应 688 个 EID，约 10.3 EID/桶）。

每页贡献 4 个 EID 到哈希桶：
- 1 个 section ID (c*)
- 1 个 inline structure ID (t*)
- 2 个 standalone structure IDs (i* 容器 + i* 叶子)

总 EID 数 = 页数 × 4（本例: 172 × 4 = 688）
