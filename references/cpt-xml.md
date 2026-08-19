# 帆软 .cpt XML 结构参考

示例基于 FineReport 11.x；`releaseVersion`、`xmlVersion` 等版本属性以当前设计器保存的模板为准。
下面的节点原文都取自设计器自己保存的文件或帆软安装目录里的示例模板，常量取自 jar 反编译。

## 目录

- [顶层骨架](#顶层骨架)
- [数据集 TableDataMap](#数据集-tabledatamap)
- [单元格 CellElementList](#单元格-cellelementlist)
- [参数面板 ReportParameterAttr](#参数面板-reportparameterattr)
- [控件节点原文](#控件节点原文)
- [样式 StyleList](#样式-stylelist)
- [常量表](#常量表)
- [单位换算](#单位换算)
- [动态表头](#动态表头)
- [自己查证的方法](#自己查证的方法)

## 顶层骨架

节点顺序不是随意的，帆软按顺序反序列化：

```xml
<?xml version="1.0" encoding="UTF-8"?>
<WorkBook xmlVersion="20211223" releaseVersion="11.5.0">
<TableDataMap>          <!-- 数据集：SQL、数据连接、数据集参数 -->
<Report class="com.fr.report.worksheet.WorkSheet" name="sheet1">
  <RowHeight/> <ColumnWidth/>   <!-- 行高列宽，逗号分隔的 CDATA 列表 -->
  <CellElementList/>            <!-- 单元格 -->
  <ReportAttrSet/>
</Report>
<ReportParameterAttr>   <!-- 参数面板 + 报表级参数 -->
<StyleList/>            <!-- 样式表，单元格 s="n" 按下标引用 -->
<!-- 后面还有 DesignerVersion / StrategyConfigsAttr 等杂项 -->
</WorkBook>
```

`StyleList` 在 `Report` 之后，但单元格靠下标 `s="0"` 引用它——所以增删样式必须同步改所有单元格的 `s`，否则引用错位，显示会花。

## 数据集 TableDataMap

```xml
<TableDataMap>
<TableData name="ds_main" class="com.fr.data.impl.DBTableData">
<Desensitizations desensitizeOpen="false"/>
<Parameters>
<Parameter>
<Attributes name="query_month"/>
<O>
<![CDATA[]]></O>
</Parameter>
</Parameters>
<Attributes maxMemRowCount="-1"/>
<Connection class="com.fr.data.impl.NameDatabaseConnection">
<DatabaseName>
<![CDATA[MY_DB]]></DatabaseName>
</Connection>
<Query>
<![CDATA[SELECT ...]]></Query>
<PageQuery>
<![CDATA[]]></PageQuery>
</TableData>
</TableDataMap>
```

`NameDatabaseConnection` 指的是帆软服务端配置好的连接名，不是 JDBC 串。连接的实际配置存在服务端 finedb 里，本地拿不到。设计器最近用过的连接名可以在 `<用户目录>/.FineReport115/FineReportEnv.xml` 的 `recentSelectedConnection` 看到。

SQL 里不能出现 `]]>`，会截断 CDATA。

## 单元格 CellElementList

`c` 是列号，`r` 是行号，`s` 是 StyleList 下标，都从 0 开始。

表头（静态文字）：

```xml
<C c="0" r="0" s="0">
<O>
<![CDATA[记录编号]]></O>
<PrivilegeControl/>
<Expand>
<cellSortAttr/>
</Expand>
</C>
```

数据列绑定：`<Expand dir="0">` 是纵向扩展，明细表靠它把数据行铺开。`<Result>` 里的 `$$$` 表示显示本单元格的值。

```xml
<C c="0" r="1" s="1">
<O t="DSColumn">
<Attributes dsName="ds_main" columnName="记录编号"/>
<Condition class="com.fr.data.condition.ListCondition"/>
<Complex/>
<RG class="com.fr.report.cell.cellattr.core.group.FunctionGrouper">
<Attr divideMode="1"/>
</RG>
<Result>
<![CDATA[$$$]]></Result>
<Parameters/>
<cellSortAttr>
<sortExpressions/>
</cellSortAttr>
</O>
<PrivilegeControl/>
<Expand dir="0">
<cellSortAttr/>
</Expand>
</C>
```

`columnName` 必须和 SQL 的输出列名（`AS` 别名）完全一致，帆软按名字取列。

`divideMode="1"` 是分组（相同值合并），明细表要列出每一行。实测设计器对明细列也写 `divideMode="1"`，因为主键列值本身不重复，效果等同列表。真要强制不分组，用 `<RG class="com.fr.report.cell.cellattr.core.group.RecordGrouper"/>`。

## 参数面板 ReportParameterAttr

```xml
<ReportParameterAttr>
<Attributes showWindow="true" delayPlaying="true" windowPosition="1" align="0" useParamsTemplate="true" currentIndex="0"/>
<PWTitle>
<![CDATA[查询条件]]></PWTitle>
<ParameterUI class="com.fr.form.main.parameter.FormParameterUI">
<Parameters/>
<Layout class="com.fr.form.ui.container.WParameterLayout">
  <WidgetName name="para"/>
  ...布局属性...
  <Widget class="...BoundsWidget">...</Widget>   <!-- 每个控件一个 -->
  <MobileWidgetList>...</MobileWidgetList>
  <Design_Width design_width="960"/>
  ...
</Layout>
<DesignAttr width="960" height="45"/>
</ParameterUI>
<Parameter>                    <!-- 报表级参数：在 </ParameterUI> 之后，无 <Parameters> 包装 -->
<Attributes name="query_month"/>
<O>
<![CDATA[]]></O>
</Parameter>
</ReportParameterAttr>
```

两个易错点：

1. **报表级 `<Parameter>` 没有 `<Parameters>` 包装**，和数据集那边写法不同。加了包装设计器读不到，面板不弹。
2. **`Layout` 下的每个 `<Widget>` 都必须是 `WAbsoluteLayout$BoundsWidget`**。`WParameterLayout` 继承 `WAbsoluteLayout`，反序列化时对每个子 Widget 做强转。直接挂真实控件 → `ClassCastException` → 整个模板降级成空白 WorkBook。日志长这样：

```
com.fr.form.ui.Label cannot be cast to com.fr.form.ui.container.WAbsoluteLayout$BoundsWidget
```

`Layout` 自己也有 `<WidgetName name="para"/>`，用正则找控件名时会把它也匹配进来，注意区分。

## 控件节点原文

### 标签 Label

文字放 `<widgetValue>`，不是 `<Label text="">`：

```xml
<Widget class="com.fr.form.ui.container.WAbsoluteLayout$BoundsWidget">
<InnerWidget class="com.fr.form.ui.Label">
<WidgetName name="label_query_month"/>
<WidgetAttr aspectRatioLocked="false" aspectRatioBackup="0.0" description="">
<MobileBookMark useBookMark="false" bookMarkName="" frozen="false" index="-1" oldWidgetName=""/>
<PrivilegeControl/>
</WidgetAttr>
<widgetValue>
<O>
<![CDATA[数据月份：]]></O>
</widgetValue>
<LabelAttr verticalcenter="true" textalign="2" autoline="false"/>
<FRFont name="SimSun" style="0" size="72"/>
<border style="0">
<color>
<FineColor color="-723724" hor="-1" ver="-1"/>
</color>
</border>
</InnerWidget>
<BoundsAttr x="10" y="12" width="100" height="21"/>
</Widget>
```

`<WidgetName name="..."/>` 的属性名是 `name`，不是 `widgetName`（`MobileWidgetList` 里才用 `widgetName`）。

### 日期控件 DateEditor

```xml
<InnerWidget class="com.fr.form.ui.DateEditor">
<WidgetName name="query_month"/>
<LabelName name="数据月份"/>
<WidgetAttr aspectRatioLocked="false" aspectRatioBackup="0.0" description="">
<MobileBookMark useBookMark="false" bookMarkName="" frozen="false" index="-1" oldWidgetName=""/>
<PrivilegeControl/>
</WidgetAttr>
<EMSG>
<![CDATA[数据月份不允许为空]]></EMSG>
<allowBlank>
<![CDATA[false]]></allowBlank>
<DateAttr format="yyyyMM"/>
<widgetValue>
<O t="XMLable" class="com.fr.base.Formula">
<Attributes>
<![CDATA[=MONTHDELTA(TODAY(), -1)]]></Attributes>
</O>
</widgetValue>
</InnerWidget>
```

- `<EMSG>` + `<allowBlank>` 是**独立子节点**，排在 `<DateAttr>` 之前。`allowBlank` 定义在 `FieldEditor`（`TextEditor`/`DateEditor` 的父类）上，但序列化成子元素，不是属性。写成 `<DateAttr allowBlank="false"/>` 不报错、也不生效。
- 不写 `returnDate` 时默认 `false`，控件返回**格式化后的字符串**（`format="yyyyMM"` → `"202608"`）。所以 SQL 里 `'${query_month}'` 当字符串用是对的，从文本框换成日期控件不需要改 SQL。要返回真正的日期对象才加 `returnDate="true"`。
- 默认值走公式时用 `<O t="XMLable" class="com.fr.base.Formula">`；常量值直接 `<O><![CDATA[值]]></O>`。
- `MONTHDELTA`/`TODAY` 在 `com/fr/function/` 下确认存在。

### 文本框 TextEditor

```xml
<InnerWidget class="com.fr.form.ui.TextEditor">
<WidgetName name="record_no"/>
<LabelName name="记录编号"/>
<WidgetAttr .../>
<TextAttr/>
<Reg class="com.fr.form.ui.reg.NoneReg"/>
<widgetValue>
<O>
<![CDATA[]]></O>
</widgetValue>
<MobileScanCodeAttr scanCode="true" textInputMode="0" isSupportManual="true" isSupportScan="true" isSupportNFC="false" nfcContentType="0"/>
<MobileTextEditAttr allowOneClickClear="true"/>
</InnerWidget>
```

### 查询按钮 FormSubmitButton

类在 `com.fr.form.parameter` 包下，不是 `com.fr.form.ui`：

```xml
<InnerWidget class="com.fr.form.parameter.FormSubmitButton">
<WidgetName name="formSubmit0"/>
<WidgetAttr .../>
<Text>
<![CDATA[查询]]></Text>
<Hotkeys>
<![CDATA[enter]]></Hotkeys>
</InnerWidget>
```

（`com.fr.form.ui.QuerySubmitButton` 这个类名是不存在的，写了会反序列化失败。）

## 样式 StyleList

单元格用下标引用，所以顺序不能乱动。`<Format>` 是第一个子节点，在 `<FRFont>` 之前：

```xml
<Style style_name="表头" full="true" border_source="-1" horizontal_alignment="0" imageLayout="1">
<FRFont name="SimSun" style="1" size="72">
<foreground>
<FineColor color="-12159859" hor="0" ver="3"/>
</foreground>
</FRFont>
<Background name="ColorBackground">
<color>
<FineColor color="-853505" hor="0" ver="1"/>
</color>
</Background>
<Border>
<Top style="1"><color><FineColor color="-2500135" hor="-1" ver="-1"/></color></Top>
...
</Border>
</Style>
```

数字格式（百分比、千分位）：

```xml
<Style imageLayout="1">
<Format class="com.fr.base.CoreDecimalFormat">
<![CDATA[#0.00%]]></Format>
<FRFont name="SimSun" style="0" size="72"/>
<Background name="NullBackground"/>
<Border/>
</Style>
```

常用格式串：`#,##0`（千分位整数）、`#,##0.00`（两位小数）、`0.00%`（百分比）、`#,##0.0`（一位小数）。
比率类字段的惯例是 SQL 输出小数（`0.1234`），格式用 `0.00%` 显示成 `12.34%`。

`FRFont` 的 `size="72"` 是 9pt（帆软按 8 倍存），`size="84"` 是 10.5pt。`style="0"` 常规、`style="1"` 加粗。

### 设计器会重写样式表

设计器保存时会重写整个文件，这些地方跟手写的不一样：

| 项 | 设计器行为 |
|----|-----------|
| 语义等价的样式 | 合并去重（内容完全相同的两个样式留一个） |
| 字体 | 换成当前主题字体，如 `WenQuanYi Micro Hei` |
| `textStyle` | 不输出 |
| CDATA | `]]></Tag>` 紧跟，不换行 |
| 换行符 | 统一 LF |

所以用户在设计器里存过的文件就是格式基准，跟它对齐比坚持自己的写法划算。

## 常量表

从 jar 反编译查证，不是猜的：

`textStyle`（`com/fr/base/Style.class`，在 `fine-core-11.0.jar`）：

| 值 | 常量 | 含义 |
|----|------|------|
| 0 | `TEXTSTYLE_WRAPTEXT` | 自动换行（**省略该属性时的默认值**） |
| 1 | `TEXTSTYLE_SINGLELINE` | 单行不换行 |
| 2 | `TEXTSTYLE_SINGLELINEADJUSTFONT` | 单行自适应字体 |
| 3 | `TEXTSTYLE_MULTILINEADJUSTFONT` | 多行自适应字体 |

注意：`textStyle="1"` 能禁止换行，但**设计器保存时不输出这个属性**，用户存一次就没了。所以表头不换行要靠加宽列宽，别依赖它。

`horizontal_alignment`（`com/fr/stable/Constants.class`，在 `fine-cbb-11.0.jar`）：

| 值 | 常量 |
|----|------|
| 0 | `CENTER` |
| 2 | `LEFT` |
| 4 | `RIGHT` |

省略即用帆软默认（文本左对齐、数值右对齐）。想让文本右对齐必须显式写 `horizontal_alignment="4"`——只建一个名叫「文本右对齐」的样式但不写这个属性，等于什么都没做（实测踩过）。

## 单位换算

列宽、行高、坐标的单位不一样，容易搞混：

| 项 | 单位 | 换算 |
|----|------|------|
| `ColumnWidth` / `RowHeight` | 内部单位 | `34290` 单位 = 1px（默认列宽 `2743200` = 80px，默认行高 `723900` = 21px） |
| `BoundsAttr` x/y/width/height | 像素 | 直接就是 px |
| `FRFont` size | pt × 8 | `72` = 9pt，`84` = 10.5pt |

表头宽度估算：中文按 16px/字（9pt 加粗留余量），英文数字按半宽，再加 20px 左右留白。

## 动态表头

表头里的「1-4 月」这类月份数通常不是固定值，而是查询月份的月份数。做法是 SQL 多输出一个 `数据月份` 字段，表头单元格用公式：

```
="1-" + TOINTEGER(RIGHT(数据月份, 2)) + "月"
```

公式单元格的 `<O>` 换成 Formula 形式，跟日期控件默认值同一个写法：

```xml
<O t="XMLable" class="com.fr.base.Formula">
<Attributes>
<![CDATA[="1-" + TOINTEGER(RIGHT(A2, 2)) + "月"]]></Attributes>
</O>
```

配套的「年初」列是上年末数（`snapshot_month` = 上一年 12 月），一般让 SQL 一次输出两个期间、用一个 `period_type` 字段区分，帆软按该维度分列。

## 自己查证的方法

遇到本文没写的节点，去示例模板里找现成的比猜可靠。帆软装完自带一批 demo：

```bash
# 找哪个示例模板用了某个类
grep -rl "com.fr.form.ui.ComboBox" --include=*.cpt \
  "<帆软安装目录>/webapps/webroot/WEB-INF/reportlets/"

# 看它的写法
python -c "
import re
c=open('<找到的文件>',encoding='utf-8',errors='replace').read()
m=re.search(r'<InnerWidget class=\"com.fr.form.ui.ComboBox\">.*?</InnerWidget>',c,re.S)
print(m.group(0))
"
```

常量直接从 jar 查：

```bash
unzip -o -q "<帆软安装目录>/webapps/webroot/WEB-INF/lib/fine-core-11.0.jar" "com/fr/base/Style.class"
javap -p -constants com/fr/base/Style.class | grep -i textstyle

# 类在哪个 jar 里不确定时先扫一遍
for j in "<帆软安装目录>/webapps/webroot/WEB-INF/lib"/*.jar; do
  unzip -l "$j" 2>/dev/null | grep -q "com/fr/stable/Constants.class" && echo "$j"
done
```

帆软的 XML 没有公开 schema，写错了设计器只会给你一个空白模板，不会告诉你哪个节点不对。所以「去示例模板和 jar 里查证」这个习惯比任何记忆都可靠。
