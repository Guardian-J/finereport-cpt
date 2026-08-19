---
name: finereport-cpt
description: 生成、修改、校验帆软 FineReport 的 .cpt 报表模板（XML 格式），包括数据集 SQL、参数查询面板（日期/文本控件、必填校验、默认值）、列宽表头、单元格样式与数字格式。只要用户提到 帆软 / FineReport / cpt / 报表模板 / 参数面板 / 查询控件 / 报表列宽表头，或要把一段 SQL 做成可查询的报表、排查「设计器打开报错/空白/参数面板不弹出/表头显示不全」，就使用本 skill——即使用户没明说「cpt」二字。手写帆软 XML 极易产出设计器无法打开的文件，本 skill 提供经实测验证的生成器与自检脚本，务必优先使用它们而不是自己拼 XML。
---

# 帆软 FineReport .cpt 模板开发

`.cpt` 是帆软报表的模板文件，本质是一份 XML。它看着像普通 XML，但节点结构对应帆软内部的 Java 对象图，**结构写错不会报「XML 格式错误」，而是反序列化时抛 `ClassCastException`，设计器把模板静默降级成空白 WorkBook**——你以为文件生成好了，用户打开却是一片空白。

所以这个领域的核心纪律是：**不要凭直觉手写 XML 节点，用已验证的生成器产出，再用断言式脚本自检。**

## 项目边界

本 skill 只定义 FineReport 模板的通用结构、生成流程和校验方法。实际项目的连接名、库名、表名、字段名、报表名、目录布局和 SQL 口径，必须以项目说明或用户提供的模板为准；不要把本文的 `MY_DB`、`fact_table` 等示例值写入生产报表。

## 工作流程

```
新建报表：  SQL ──► gen_cpt.py ──► .cpt ──► check_cpt.py ──► 交付
改已有报表：SQL ──► sync_sql.py ─────┘（只换 SQL，保住设计器里的调整）
```

1. **先把 SQL 定稿**。列名（`AS 中文别名`）和参数占位符（`${param}`）决定了 cpt 的表头与查询面板，SQL 改了 cpt 就得跟着改，所以别倒过来做。
2. **新建**：`python scripts/gen_cpt.py 报表_SQL.sql -o 报表.cpt --connection 数据连接名`
3. **改 SQL**：`python scripts/sync_sql.py 报表.cpt 报表_SQL.sql` —— 只替换 `<Query>` 和参数节点，单元格、样式、数字格式一个字节不动。**用户在设计器里调过的报表一定走这条路**，重新生成会把他们的工作全冲掉。
4. **自检**：`python scripts/check_cpt.py 报表.cpt`，通过才算交付。

三个脚本都支持 `--help` 和 `--self-test`，不确定参数时先看 `--help`。批量用法：

```bash
python scripts/sync_sql.py reports/ --sql-dir reports --exclude sample.cpt
python scripts/check_cpt.py reports/ -c MY_DB --sql-dir reports
```

## 生成器做了什么

`gen_cpt.py` 从 SQL 里解析出两样东西，其余全部按实测验证过的骨架填充：

- **表头**：`SELECT` 里的 `AS 别名`，顺序即列顺序
- **参数**：`${xxx}` 占位符，按出现顺序去重

然后输出一张两行的报表——第 0 行是表头文字，第 1 行是数据列绑定（`DSColumn` 纵向扩展），配上参数查询面板和列宽。

参数控件类型按名字推断：含 `month`/`date`/`日期`/`月份` 的给日期控件（`DateEditor`），其余给文本框（`TextEditor`）。推断不对就用 `--date-params` / `--text-params` 显式指定。

日期参数默认必填、默认值取上月（`=MONTHDELTA(TODAY(), -1)`）——这是月度报表的常见口径；如果用户的报表不是这个口径，用 `--optional` 和 `--default` 改。

## 五个必须记住的结构约束

这些都是实测踩出来的，写错的后果各不相同，值得单独记：

**1. 参数控件必须包一层 BoundsWidget。** `WParameterLayout` 继承 `WAbsoluteLayout`，它的每个 `<Widget>` 子节点都会被强转成 `WAbsoluteLayout$BoundsWidget`。真实控件要放进 `<InnerWidget>`，位置写在外层的 `<BoundsAttr>`：

```xml
<Widget class="com.fr.form.ui.container.WAbsoluteLayout$BoundsWidget">
<InnerWidget class="com.fr.form.ui.DateEditor">
  ...
</InnerWidget>
<BoundsAttr x="110" y="12" width="140" height="21"/>
</Widget>
```

把控件直接挂在 Layout 下面 → `ClassCastException` → 模板变空白。这是最容易犯也最致命的一个。

**2. 参数要在两处声明，缺一处就传不进值。**

| 位置 | 作用 | 缺失后果 |
|------|------|----------|
| `<TableDataMap>` → `<TableData>` → `<Parameters>` | 数据集参数，供 SQL `${xxx}` 取值 | SQL 拿不到值 |
| `<ReportParameterAttr>` 下的 `<Parameter>` | 报表级参数 | 打开报表不弹参数面板 |

注意报表级 `<Parameter>` 的位置：在 `</ParameterUI>` **之后**，且**没有** `<Parameters>` 包装。这跟数据集那边的写法不一样，容易照抄错。

**3. 必填校验是独立子节点，不是属性。** `allowBlank` 定义在 `FieldEditor` 上，但序列化成独立元素，配一个 `<EMSG>` 提示语，且都排在 `<DateAttr>`/`<TextAttr>` **前面**：

```xml
<EMSG>
<![CDATA[数据月份不允许为空]]></EMSG>
<allowBlank>
<![CDATA[false]]></allowBlank>
<DateAttr format="yyyyMM"/>
```

写成 `<DateAttr format="yyyyMM" allowBlank="false"/>` 不报错，但校验完全不生效——属于最难发现的那类错。

**4. 日期控件不写 `returnDate` 时返回格式化后的字符串。** `<DateAttr format="yyyyMM"/>` 配上默认的 `returnDate=false`，控件传给 SQL 的就是 `"yyyyMM"` 格式的字符串，所以 SQL 里 `'${query_month}'` 加引号当字符串用是对的，不需要为了换成日期控件去改 SQL。

**5. 表头不换行靠列宽，不靠 `textStyle`。** 帆软默认 `textStyle=0`（WRAPTEXT，自动换行），理论上写 `textStyle="1"`（SINGLELINE）能禁止换行——但**设计器保存时不输出这个属性，用户存一次就丢了**。可靠做法是把列宽放到足够容纳表头文字，生成器已经这么做（中文按 16px/字 + 20px 留白，并与字段类型基准宽度取大值）。

## 设计器优先

用户在帆软设计器里保存过的文件，是格式的最终裁判。设计器会重写整个文件，你手写的任何与它不一致的细节，用户下次保存就没了。

所以当用户说「我在设计器里改了 X，你学习一下」时，正确反应是**读那个文件，把它当模板**，而不是照自己的理解去改别的文件。这次实测中，设计器至少在这些地方与手写产物不同：

| 项 | 设计器写法 |
|----|-----------|
| CDATA | `]]></Tag>` 结束标签紧跟，不换行 |
| 样式表 | 会合并语义等价的样式（内容完全相同的两个样式会被去重掉一个） |
| 字体 | 换成当前主题字体（如 `WenQuanYi Micro Hei`） |
| `textStyle` | 不输出 |
| 换行符 | 统一 LF |

如果多个报表要跟随同一份设计器产物，`gen_cpt.py --template 设计器文件.cpt` 会以它的样式表和控件写法为准来生成其余文件。

## 校验器检查什么

`check_cpt.py` 是断言式的，任何一条不过就抛 `AssertionError` 并指出报表名和具体问题。它覆盖：

- XML 可解析（挡住会降级成空白模板的文件）
- 数据连接一致，没有残留 `FRDemo` 之类的示例连接
- 内嵌 SQL 与同名 `.sql` 文件一致（防止改了 SQL 忘了同步）
- **SQL 占位符 == 数据集参数 == 报表参数**，按顺序比，且不允许重复
- 参数面板控件嵌套正确、每个参数一个输入控件、有查询按钮
- 样式索引都有定义、表头行样式统一、没有残留 `textStyle`
- 每列宽度够放完整表头
- SQL 常见问题：`AS AS`、空 `divide(`、除法没做除零保护、JOIN 里裸写 `${param}`

用顺序列表而不是集合来比参数，是因为集合会把重复的 `<Parameter>` 节点吃掉——设计器里会显示成两个同名参数，但集合比较看不出来。这个坑真踩过。

## 常见故障对照

| 现象 | 原因 |
|------|------|
| 设计器打开是空白模板 | 参数控件没包 BoundsWidget，或其他反序列化失败 |
| 打开不弹参数面板 | 缺 `<ReportParameterAttr>` 下的报表级 `<Parameter>` |
| 参数面板有控件但 SQL 查不到数据 | 缺数据集 `<Parameters>` |
| 必填校验点了查询也能空着过 | `allowBlank` 写成了属性而不是独立节点 |
| 表头文字显示不全/被截断 | 列宽不够；`textStyle` 靠不住，要加宽列 |
| 参数面板出现两个同名参数 | `<Parameter>` 节点重复，用 `check_cpt.py` 能查出来 |
| 报错「数据库连接失败 驱动器未找到」 | 连接名写成了帆软自带示例连接 |

## 深入细节

需要写生成器没覆盖的东西时（分组汇总、条件属性、图表、动态表头公式等），先读 `references/cpt-xml.md`，里面有完整骨架标注、各控件节点原文、单位换算和从帆软 jar 里查证出来的常量表（`textStyle`、`horizontal_alignment`、数字格式）。

不确定某个节点怎么写时，最可靠的办法是去帆软安装目录的示例模板里找现成的：

```bash
grep -rl "你要找的类名" --include=*.cpt "<帆软安装目录>/webapps/webroot/WEB-INF/reportlets/"
```

常量则可以直接从 jar 里查证，比猜可靠：

```bash
unzip -o -q "<帆软安装目录>/webapps/webroot/WEB-INF/lib/fine-core-11.0.jar" "com/fr/base/Style.class"
javap -p -constants com/fr/base/Style.class | grep -i textstyle
```

这个「去示例模板和 jar 里查证，而不是凭印象写」的习惯，是整个 skill 里最值钱的部分——帆软的 XML 没有公开 schema，猜错的成本是用户打开一个空白模板。
