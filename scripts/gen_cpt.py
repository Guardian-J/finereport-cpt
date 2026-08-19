# -*- coding: utf-8 -*-
"""
从 SQL 生成帆软 FineReport .cpt 模板。

为什么要有这个脚本：帆软 .cpt 的节点结构对应其内部 Java 对象图，写错不会报
XML 语法错误，而是反序列化时抛 ClassCastException，设计器把模板静默降级成
空白 WorkBook。手写这些节点实测反复失败，所以这里把设计器实际保存出来的
骨架固化下来，只让 SQL 决定表头和参数。

用法：
    python gen_cpt.py 报表_SQL.sql -o 报表.cpt --connection MY_DB

    # 参数控件类型推断不对时显式指定
    python gen_cpt.py x.sql -o x.cpt -c MY_DB --date-params query_month \\
        --text-params record_no,owner_name

    # 金额列的单位口径（决定小数位），不想要数字格式就 --no-format
    python gen_cpt.py x.sql -o x.cpt -c MY_DB --unit 万元

    # 跟随设计器保存过的文件（以它的样式表为准）
    python gen_cpt.py x.sql -o x.cpt -c MY_DB --template 设计器产物.cpt

自检：python gen_cpt.py --self-test
"""
import argparse
import os
import re
import sys
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------- 单位与布局
UNIT_PER_PX = 34290          # 帆软列宽/行高单位per像素（2743200 单位 = 80px）
ROW_PX = 21                  # 单行文字高度
CHAR_PX = 16                 # 表头中文按 16px/字估算（加粗字体留余量）
PADDING_PX = 20              # 列宽左右留白
MIN_COL_PX = 70              # 列宽下限

# 参数面板控件布局
LABEL_W, WIDGET_W, BTN_W, GAP, X0, WIDGET_Y = 100, 140, 80, 16, 10, 12
PANEL_W, PANEL_H = 960, 45

# 字段类型基准宽度（px）。文本类通常较长，比率类内容短
TYPE_BASE_PX = {"文本": 110, "比率": 80, "金额": 95, "整数": 85, "人数": 90}

# 字段类型按表头关键词识别，用于列宽基准和数字格式。判断顺序很重要：
# 「完成率」同时含业务词和「率」，必须先判比率，否则会被当成金额。
RATE_KW = ("率", "占比", "比例", "系数")
PERSON_KW = ("人数", "人次")
INT_KW = ("个数", "笔数", "件数", "数量", "台数", "份数", "天数", "个月")
TEXT_KW = ("名称", "公司", "部门", "来源", "分类", "状态", "形式", "城市", "省份",
           "编号", "日期", "口径", "是否", "组织", "客户", "编码", "业务",
           "期间", "月份", "备注", "说明", "类型", "地区", "级别", "大区")

# 单位口径 → 金额列的数字格式。万元/亿元口径的数已经被 SQL 缩小过，
# 亿元还要留两位小数，不然小额记录全被抹成 0
UNIT_FORMAT = {"原值": "#,##0.00", "万元": "#,##0", "亿元": "#,##0.00"}
DEFAULT_UNIT = "原值"

# 类型 → 帆软数字格式串。文本不设格式，保持原样输出；金额看 UNIT_FORMAT
TYPE_FORMAT = {"比率": "0.00%", "整数": "#,##0", "人数": "#,##0", "文本": None}

# 参数名含这些词时默认给日期控件
DATE_HINT = ("month", "date", "day", "year", "日期", "月份", "年月")

DS_NAME = "ds_main"
DEFAULT_DATE_FORMAT = "yyyyMM"
DEFAULT_DATE_VALUE = "=MONTHDELTA(TODAY(), -1)"


# ---------------------------------------------------------------- SQL 解析
def strip_comments(sql):
    """去掉 /* */ 与 -- 注释，避免注释里的 AS/${} 干扰解析"""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    return re.sub(r"--[^\n]*", " ", sql)


def split_top_level(text, sep=","):
    """按分隔符切分，但跳过括号内与引号内的分隔符"""
    parts, depth, quote, buf = [], 0, None, []
    for ch in text:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == sep and depth == 0:
            parts.append("".join(buf))
            buf = []
            continue
        buf.append(ch)
    parts.append("".join(buf))
    return parts


def extract_headers(sql):
    """取最外层 SELECT 的 AS 别名，顺序即列顺序。

    只认显式 AS 别名：帆软数据集的列名要和 SQL 输出列名对得上，
    没写 AS 的表达式列名由数据库决定，不可靠，所以要求显式声明。
    """
    body = strip_comments(sql)
    # 跳过 CTE：从最后一个顶层 SELECT ... FROM 里取
    best = None
    for m in re.finditer(r"\bSELECT\b", body, re.I):
        depth = body.count("(", 0, m.start()) - body.count(")", 0, m.start())
        if depth == 0:
            best = m
    assert best, "SQL 中未找到顶层 SELECT"
    tail = body[best.end():]
    # 找配对的 FROM（跳过子查询里的 FROM）
    depth, end = 0, None
    for m in re.finditer(r"[()]|\bFROM\b", tail, re.I):
        tok = m.group(0)
        if tok == "(":
            depth += 1
        elif tok == ")":
            depth -= 1
        elif depth == 0:
            end = m.start()
            break
    assert end is not None, "SQL 顶层 SELECT 后未找到 FROM"

    heads = []
    for col in split_top_level(tail[:end]):
        m = re.search(r"\bAS\s+([`\"\[]?)([^\s,`\"\]]+)\1\s*$", col.strip(), re.I)
        if m:
            heads.append(m.group(2))
    assert heads, "未解析到任何 AS 别名，请为每个输出列显式写 AS 中文别名"
    dup = [h for h in heads if heads.count(h) > 1]
    assert not dup, "列别名重复：%s（帆软按列名绑定，重复会取错列）" % sorted(set(dup))
    return heads


def extract_params(sql):
    """取 ${xxx} 占位符，按出现顺序去重（面板控件按这个顺序排布）。

    先去注释：SQL 头部通常有大段参数说明，里面写着 ${xxx} 举例，
    那些不是真占位符，混进来会在面板上多出没用的控件。
    """
    return list(dict.fromkeys(re.findall(r"\$\{(\w+)\}", strip_comments(sql))))


# ---------------------------------------------------------------- 宽度计算
def field_type(header):
    """按表头关键词猜字段类型。比率必须第一个判，见 RATE_KW 上方的注释。"""
    if any(k in header for k in RATE_KW):
        return "比率"
    if any(k in header for k in PERSON_KW):
        return "人数"
    if any(k in header for k in INT_KW):
        return "整数"
    if any(k in header for k in TEXT_KW):
        return "文本"
    return "金额"


def col_format(header, unit):
    """列的帆软数字格式串，文本列返回 None（不设格式）。

    金额列的小数位取决于单位口径：SQL 已经除成万元的数留整数就够，
    亿元口径必须保留两位，否则小额数据全显示成 0。
    """
    t = field_type(header)
    return UNIT_FORMAT[unit] if t == "金额" else TYPE_FORMAT[t]


def header_px(header):
    """表头文字所需宽度：中文按整宽，英文/数字按半宽"""
    w = sum(CHAR_PX if ord(ch) > 127 else CHAR_PX * 0.55 for ch in header)
    return int(w) + PADDING_PX


def col_px(header):
    """列宽 = max(类型基准, 表头所需, 下限)。

    不设上限：表头一行放不下就会换行，宁可宽一点。帆软的 textStyle=SINGLELINE
    看似能禁止换行，但设计器保存时不输出该属性，靠不住。
    """
    return max(TYPE_BASE_PX[field_type(header)], header_px(header), MIN_COL_PX)


# ---------------------------------------------------------------- XML 片段
def cd(text):
    """CDATA，按设计器写法让 ]]> 紧跟结束标签"""
    return "<![CDATA[%s]]>" % text


def label_widget(param, text, x):
    return """<Widget class="com.fr.form.ui.container.WAbsoluteLayout$BoundsWidget">
<InnerWidget class="com.fr.form.ui.Label">
<WidgetName name="label_%s"/>
<WidgetAttr aspectRatioLocked="false" aspectRatioBackup="0.0" description="">
<MobileBookMark useBookMark="false" bookMarkName="" frozen="false" index="-1" oldWidgetName=""/>
<PrivilegeControl/>
</WidgetAttr>
<widgetValue>
<O>
%s</O>
</widgetValue>
<LabelAttr verticalcenter="true" textalign="2" autoline="false"/>
<FRFont name="SimSun" style="0" size="72"/>
<border style="0">
<color>
<FineColor color="-723724" hor="-1" ver="-1"/>
</color>
</border>
</InnerWidget>
<BoundsAttr x="%d" y="%d" width="%d" height="%d"/>
</Widget>
""" % (param, cd(text + "："), x, WIDGET_Y, LABEL_W, ROW_PX)


def date_widget(param, label, x, fmt, default, required):
    """日期控件。

    必填校验的 <EMSG>/<allowBlank> 是独立子节点且排在 <DateAttr> 前面——
    写成 <DateAttr allowBlank="false"/> 属性不报错但完全不生效。
    不写 returnDate 时（默认 false）控件返回格式化后的字符串，
    所以 SQL 里 '${param}' 当字符串用是对的。
    """
    validate = ""
    if required:
        validate = "<EMSG>\n%s</EMSG>\n<allowBlank>\n%s</allowBlank>\n" % (
            cd("%s不允许为空" % label), cd("false"))
    if default.startswith("="):
        value = ('<O t="XMLable" class="com.fr.base.Formula">\n<Attributes>\n%s</Attributes>\n</O>'
                 % cd(default))
    else:
        value = "<O>\n%s</O>" % cd(default)
    return """<Widget class="com.fr.form.ui.container.WAbsoluteLayout$BoundsWidget">
<InnerWidget class="com.fr.form.ui.DateEditor">
<WidgetName name="%s"/>
<LabelName name="%s"/>
<WidgetAttr aspectRatioLocked="false" aspectRatioBackup="0.0" description="">
<MobileBookMark useBookMark="false" bookMarkName="" frozen="false" index="-1" oldWidgetName=""/>
<PrivilegeControl/>
</WidgetAttr>
%s<DateAttr format="%s"/>
<widgetValue>
%s
</widgetValue>
</InnerWidget>
<BoundsAttr x="%d" y="%d" width="%d" height="%d"/>
</Widget>
""" % (param, label, validate, fmt, value, x, WIDGET_Y, WIDGET_W, ROW_PX)


def text_widget(param, label, x, required):
    validate = ""
    if required:
        validate = "<EMSG>\n%s</EMSG>\n<allowBlank>\n%s</allowBlank>\n" % (
            cd("%s不允许为空" % label), cd("false"))
    return """<Widget class="com.fr.form.ui.container.WAbsoluteLayout$BoundsWidget">
<InnerWidget class="com.fr.form.ui.TextEditor">
<WidgetName name="%s"/>
<LabelName name="%s"/>
<WidgetAttr aspectRatioLocked="false" aspectRatioBackup="0.0" description="">
<MobileBookMark useBookMark="false" bookMarkName="" frozen="false" index="-1" oldWidgetName=""/>
<PrivilegeControl/>
</WidgetAttr>
%s<TextAttr/>
<Reg class="com.fr.form.ui.reg.NoneReg"/>
<widgetValue>
<O>
%s</O>
</widgetValue>
<MobileScanCodeAttr scanCode="true" textInputMode="0" isSupportManual="true" isSupportScan="true" isSupportNFC="false" nfcContentType="0"/>
<MobileTextEditAttr allowOneClickClear="true"/>
</InnerWidget>
<BoundsAttr x="%d" y="%d" width="%d" height="%d"/>
</Widget>
""" % (param, label, validate, cd(""), x, WIDGET_Y, WIDGET_W, ROW_PX)


def submit_widget(x):
    return """<Widget class="com.fr.form.ui.container.WAbsoluteLayout$BoundsWidget">
<InnerWidget class="com.fr.form.parameter.FormSubmitButton">
<WidgetName name="formSubmit0"/>
<WidgetAttr aspectRatioLocked="false" aspectRatioBackup="0.0" description="">
<MobileBookMark useBookMark="false" bookMarkName="" frozen="false" index="-1" oldWidgetName=""/>
<PrivilegeControl/>
</WidgetAttr>
<Text>
%s</Text>
<Hotkeys>
%s</Hotkeys>
</InnerWidget>
<BoundsAttr x="%d" y="%d" width="%d" height="%d"/>
</Widget>
""" % (cd("查询"), cd("enter"), x, WIDGET_Y, BTN_W, ROW_PX)


STYLE_RE = re.compile(r"<Style\b.*?</Style>", re.S)


def format_style(base, fmt):
    """在默认样式基础上派生一个带数字格式的样式。

    `<Format>` 必须是 `<Style>` 的第一个子节点（在 `<FRFont>` 之前），
    位置放错帆软读不到格式。
    """
    m = re.match(r"(<Style\b[^>]*>)(.*)", base, re.S)
    assert m, "样式块格式不对：%s" % base[:60]
    open_tag = re.sub(r'style_name="[^"]*"', 'style_name="格式 %s"' % fmt, m.group(1))
    return '%s\n<Format class="com.fr.base.CoreDecimalFormat">\n%s</Format>%s' % (
        open_tag, cd(fmt), m.group(2))


def build_stylelist(headers, unit, stylelist, with_format=True):
    """返回 (样式表 XML, 每列数据行用的样式下标)。

    下标 0 是表头样式、1 是默认样式，用到的数字格式各追加一个样式。
    追加在末尾而不是插在中间，是为了不打乱已有的 0/1 下标——单元格靠
    下标 `s="n"` 引用样式，插在中间会让所有引用错位。
    """
    blocks = STYLE_RE.findall(stylelist)
    assert len(blocks) >= 2, "样式表至少要有表头与默认两个样式，实际 %d 个" % len(blocks)

    fmts, col_style = [], []
    for h in headers:
        fmt = col_format(h, unit) if with_format else None
        if fmt is None:
            col_style.append(1)
            continue
        if fmt not in fmts:
            fmts.append(fmt)
        col_style.append(len(blocks) + fmts.index(fmt))

    if not fmts:
        return stylelist, col_style
    extra = "\n".join(format_style(blocks[1], f) for f in fmts)
    i = stylelist.rfind("</StyleList>")
    return stylelist[:i] + extra + "\n" + stylelist[i:], col_style


def param_nodes(params):
    """参数声明节点，数据集与报表两处共用同一份写法"""
    return "".join("<Parameter>\n<Attributes name=\"%s\"/>\n<O>\n%s</O>\n</Parameter>\n"
                   % (p, cd("")) for p in params)


DEFAULT_STYLELIST = """<StyleList>
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
<Top style="1">
<color>
<FineColor color="-2500135" hor="-1" ver="-1"/>
</color>
</Top>
<Bottom style="1">
<color>
<FineColor color="-2500135" hor="-1" ver="-1"/>
</color>
</Bottom>
<Left style="1">
<color>
<FineColor color="-2500135" hor="-1" ver="-1"/>
</color>
</Left>
<Right style="1">
<color>
<FineColor color="-2500135" hor="-1" ver="-1"/>
</color>
</Right>
</Border>
</Style>
<Style style_name="默认" full="true" border_source="-1" imageLayout="1">
<FRFont name="SimSun" style="0" size="72"/>
<Background name="NullBackground"/>
<Border/>
</Style>
</StyleList>"""


def build_cells(headers, col_style):
    """两行报表：第 0 行表头文字（样式 0），第 1 行数据列绑定（纵向扩展）。

    数据行的样式按列取 col_style——数字列要带格式，文本列用默认样式。
    """
    out = []
    for i, h in enumerate(headers):
        out.append('<C c="%d" r="0" s="0">\n<O>\n%s</O>\n<PrivilegeControl/>\n'
                   '<Expand>\n<cellSortAttr/>\n</Expand>\n</C>\n' % (i, cd(h)))
    for i, h in enumerate(headers):
        out.append(
            '<C c="%d" r="1" s="%d">\n<O t="DSColumn">\n'
            '<Attributes dsName="%s" columnName="%s"/>\n'
            '<Condition class="com.fr.data.condition.ListCondition"/>\n<Complex/>\n'
            '<RG class="com.fr.report.cell.cellattr.core.group.FunctionGrouper">\n'
            '<Attr divideMode="1"/>\n</RG>\n<Result>\n%s</Result>\n<Parameters/>\n'
            '<cellSortAttr>\n<sortExpressions/>\n</cellSortAttr>\n</O>\n'
            '<PrivilegeControl/>\n<Expand dir="0">\n<cellSortAttr/>\n</Expand>\n</C>\n'
            % (i, col_style[i], DS_NAME, h, cd("$$$")))
    return "".join(out)


def build_panel(params, labels, date_params, fmt, default, optional):
    """参数面板：每个参数一个标签 + 一个输入控件，末尾一个查询按钮。

    所有控件都必须包在 WAbsoluteLayout$BoundsWidget 里，真实控件放 <InnerWidget>。
    WParameterLayout 继承 WAbsoluteLayout，会把每个 Widget 子节点强转成
    BoundsWidget，直接挂真实控件会抛 ClassCastException，模板降级为空白。
    """
    blocks, x = [], X0
    for p in params:
        label = labels.get(p, p)
        blocks.append(label_widget(p, label, x))
        x += LABEL_W
        if p in date_params:
            blocks.append(date_widget(p, label, x, fmt, default, p not in optional))
        else:
            blocks.append(text_widget(p, label, x, p not in optional))
        x += WIDGET_W + GAP
    blocks.append(submit_widget(x))
    mobile = "".join('<Widget widgetName="%s"/>\n' % p for p in params + ["formSubmit0"])
    return "".join(blocks), mobile, max(x + BTN_W + X0, PANEL_W)


def build_param_attr(params, labels, date_params, fmt, default, optional):
    """整个 <ReportParameterAttr>：参数面板 + 报表级参数节点。

    单独成函数是为了让 fix_cpt.py 能原地重建已有报表的参数面板——
    面板节点太细，手工补远不如整块换成这里验证过的版本。
    """
    widgets, mobile, panel_w = build_panel(
        params, labels, date_params, fmt, default, optional)
    return """<ReportParameterAttr>
<Attributes showWindow="true" delayPlaying="true" windowPosition="1" align="0" useParamsTemplate="true" currentIndex="0"/>
<PWTitle>
%(title)s</PWTitle>
<ParameterUI class="com.fr.form.main.parameter.FormParameterUI">
<Parameters/>
<Layout class="com.fr.form.ui.container.WParameterLayout">
<WidgetName name="para"/>
<WidgetAttr aspectRatioLocked="false" aspectRatioBackup="0.0" description="">
<MobileBookMark useBookMark="false" bookMarkName="" frozen="false" index="-1" oldWidgetName=""/>
<PrivilegeControl/>
</WidgetAttr>
<FollowingTheme borderStyle="false"/>
<Margin top="0" left="0" bottom="0" right="0"/>
<Border>
<border style="0" borderRadius="0" type="0" borderStyle="0">
<color>
<FineColor color="-723724" hor="-1" ver="-1"/>
</color>
</border>
<WidgetTitle>
<O>
%(newtitle)s</O>
<FRFont name="SimSun" style="0" size="72"/>
<Position pos="0"/>
</WidgetTitle>
<Alpha alpha="1.0"/>
</Border>
<Background name="ColorBackground">
<color>
<FineColor color="-526086" hor="-1" ver="-1"/>
</color>
</Background>
<LCAttr vgap="0" hgap="0" compInterval="0"/>
%(widgets)s<ShowBookmarks showBookmarks="false"/>
<Sorted sorted="false"/>
<MobileWidgetList>
%(mobile)s</MobileWidgetList>
<FrozenWidgets/>
<MobileBookMarkStyle class="com.fr.form.ui.mobile.impl.DefaultMobileBookMarkStyle"/>
<Display display="true"/>
<DelayDisplayContent delay="true"/>
<UseParamsTemplate use="true"/>
<paramFireStopEdit fireEvent="false"/>
<Position position="0"/>
<Design_Width design_width="%(panel_w)d"/>
<NameTagModified/>
<WidgetNameTagMap/>
<ParamAttr class="com.fr.report.mobile.DefaultMobileParamStyle"/>
<ParamStyle class="com.fr.form.ui.mobile.impl.DefaultMobileParameterStyle"/>
<FollowingTheme background="true"/>
</Layout>
<DesignAttr width="%(panel_w)d" height="%(panel_h)d"/>
</ParameterUI>
%(pnodes)s</ReportParameterAttr>""" % dict(
        title=cd("查询条件"), newtitle=cd("新建标题"), widgets=widgets,
        mobile=mobile, panel_w=panel_w, panel_h=PANEL_H,
        pnodes=param_nodes(params))


def build_cpt(sql, connection, headers, params, labels, date_params,
              fmt, default, optional, stylelist,
              unit=DEFAULT_UNIT, with_format=True):
    assert "]]>" not in sql, "SQL 含 ]]> 会截断 CDATA，请改写 SQL"
    stylelist, col_style = build_stylelist(headers, unit, stylelist, with_format)
    widths = ",".join(str(col_px(h) * UNIT_PER_PX) for h in headers)
    # 行高：表头行 + 数据行，多给几行默认高度，设计器里加行时不用重设
    heights = ",".join([str(ROW_PX * UNIT_PER_PX)] * 2 + [str(ROW_PX * UNIT_PER_PX)] * 9)
    param_attr = build_param_attr(params, labels, date_params, fmt, default, optional)

    return """<?xml version="1.0" encoding="UTF-8"?>
<WorkBook xmlVersion="20211223" releaseVersion="11.5.0">
<TableDataMap>
<TableData name="%(ds)s" class="com.fr.data.impl.DBTableData">
<Desensitizations desensitizeOpen="false"/>
<Parameters>
    %(pnodes)s</Parameters>
<Attributes maxMemRowCount="-1"/>
<Connection class="com.fr.data.impl.NameDatabaseConnection">
<DatabaseName>
%(conn)s</DatabaseName>
</Connection>
<Query>
%(sql)s</Query>
<PageQuery>
%(empty)s</PageQuery>
</TableData>
</TableDataMap>
<Report class="com.fr.report.worksheet.WorkSheet" name="sheet1">
<ReportPageAttr>
<HR/>
<FR/>
<HC/>
<FC/>
<USE REPEAT="false" PAGE="false" WRITE="false"/>
</ReportPageAttr>
<ColumnPrivilegeControl/>
<RowPrivilegeControl/>
<RowHeight defaultValue="723900">
%(heights)s</RowHeight>
<ColumnWidth defaultValue="2743200">
%(widths)s</ColumnWidth>
<CellElementList>
%(cells)s</CellElementList>
<ReportAttrSet>
<ReportSettings headerHeight="0" footerHeight="0">
<PaperSetting/>
<FollowingTheme background="true"/>
<Background name="ColorBackground">
<color>
<FineColor color="-1" hor="-1" ver="-1"/>
</color>
</Background>
</ReportSettings>
</ReportAttrSet>
<PrivilegeControl/>
</Report>
%(param_attr)s
%(stylelist)s
<DesensitizationList/>
<DesignerVersion DesignerVersion="LAA"/>
<PreviewType PreviewType="0"/>
<StrongestControlAttr class="com.fr.widgettheme.control.attr.WidgetDisplayEnhanceMarkAttr">
<StrongestControlAttr widgetEnhance="false"/>
</StrongestControlAttr>
<StrategyConfigsAttr class="com.fr.esd.core.strategy.persistence.StrategyConfigsAttr">
<StrategyConfigs>
<StrategyConfig dsName="%(ds)s" enabled="false" useGlobal="true" shouldMonitor="true" shouldEvolve="false" scheduleBySchema="false" timeToLive="1500000" timeToIdle="86400000" updateInterval="1500000" terminalTime="" updateSchema="0 0 8 * * ? *" activeInitiation="false"/>
</StrategyConfigs>
</StrategyConfigsAttr>
<ForkIdAttrMark class="com.fr.base.iofile.attr.ForkIdAttrMark">
<ForkIdAttrMark forkId="00000000-0000-0000-0000-000000000000"/>
</ForkIdAttrMark>
<TemplateIdAttMark class="com.fr.base.iofile.attr.TemplateIdAttrMark">
<TemplateIdAttMark TemplateId="00000000-0000-0000-0000-000000000000"/>
</TemplateIdAttMark>
</WorkBook>
  """ % dict(ds=DS_NAME, conn=cd(connection), sql=cd(sql),
             pnodes=param_nodes(params),
             empty=cd(""), heights=cd(heights), widths=cd(widths),
           cells=build_cells(headers, col_style),
           param_attr=param_attr, stylelist=stylelist)


# ---------------------------------------------------------------- 自检
def self_test():
    sql = """-- 注释里的 AS 别名 与 ${fake} 不该被解析
SELECT
    t.record_no        AS 记录编号,
    m.group_name       AS 所属分组名称,
    round(t.amount / 10000)     AS 累计金额,
    if(t.denominator > 0, t.numerator / t.denominator, 0) AS 完成率
FROM fact_table t
LEFT JOIN dim_group m ON t.group_no = m.group_no
    AND m.snapshot_month = (SELECT max(snapshot_month) FROM dim_group
                            WHERE snapshot_month <= '${query_month}')
WHERE t.snapshot_month LIKE concat('${query_month}', '%')
    AND t.record_no LIKE '%${record_no}%'
;"""
    heads = extract_headers(sql)
    assert heads == ["记录编号", "所属分组名称", "累计金额", "完成率"], heads
    params = extract_params(sql)
    assert params == ["query_month", "record_no"], params

    # 注释里的占位符不该混进来，子查询里的 FROM 不该被当成顶层 FROM
    assert "fake" not in params, "注释中的 ${} 被误解析"
    # 长表头必须比短表头宽，且能放下自身文字
    assert col_px("本年累计销售收入") > col_px("是否新增"), "长表头未变宽"
    for h in heads:
        assert col_px(h) >= header_px(h), "%s: 列宽放不下表头" % h
    # 类型识别：比率优先于金额，人数/整数不能被当成金额
    assert field_type("完成率") == "比率", "含「率」应判为比率"
    assert field_type("期末金额") == "金额"
    assert field_type("在册人数") == "人数" and field_type("记录笔数") == "整数"
    # 数字格式随单位口径变化，文本列不设格式
    assert col_format("期末金额", "万元") == "#,##0"
    assert col_format("期末金额", "亿元") == "#,##0.00"
    assert col_format("完成率", "万元") == "0.00%"
    assert col_format("所属分组名称", "万元") is None

    cpt = build_cpt(sql, "TEST_CONN", heads, params,
                    {"query_month": "统计月份", "record_no": "记录编号"},
                     {"query_month"}, DEFAULT_DATE_FORMAT, DEFAULT_DATE_VALUE, set(),
                    DEFAULT_STYLELIST, unit="万元")
    root = ET.fromstring(cpt)   # 解析失败说明会降级成空白模板

    # 参数三方一致：SQL 占位符 == 数据集参数 == 报表参数，且不重复
    ds = cpt[: cpt.find("</TableDataMap>")]
    rp = cpt[cpt.find("</ParameterUI>"): cpt.find("</ReportParameterAttr>")]
    ds_p = re.findall(r'<Attributes name="(\w+)"/>', ds)
    rp_p = re.findall(r'<Attributes name="(\w+)"/>', rp)
    assert ds_p == rp_p == params, "参数不一致 数据集=%s 报表=%s SQL=%s" % (ds_p, rp_p, params)

    # 控件嵌套：Layout 下每个 Widget 都必须是 BoundsWidget 且带 InnerWidget
    layout = next(e for e in root.iter("Layout")
                  if e.get("class", "").endswith("WParameterLayout"))
    ws = layout.findall("Widget")
    assert len(ws) == 5, "2 参数应有 2标签+2控件+1按钮 = 5 个控件，实际 %d" % len(ws)
    for w in ws:
        assert w.get("class").endswith("WAbsoluteLayout$BoundsWidget"), w.get("class")
        assert w.find("InnerWidget") is not None and w.find("BoundsAttr") is not None

    # 必填校验必须是独立子节点（写成属性不报错但不生效）
    de = next(e for e in root.iter("InnerWidget")
              if e.get("class") == "com.fr.form.ui.DateEditor")
    ab = de.find("allowBlank")
    assert ab is not None and ab.text.strip() == "false", "allowBlank 节点缺失"
    assert de.find("DateAttr").get("format") == "yyyyMM"
    assert "MONTHDELTA" in de.find("widgetValue/O/Attributes").text

    # 控件不重叠：按 x 排序后，前一个的右边界不超过后一个的左边界
    bounds = sorted((int(w.find("BoundsAttr").get("x")),
                     int(w.find("BoundsAttr").get("width"))) for w in ws)
    for (x1, w1), (x2, _) in zip(bounds, bounds[1:]):
        assert x1 + w1 <= x2, "控件重叠：x=%d w=%d 压到 x=%d" % (x1, w1, x2)

    # 列数与表头数一致
    widths = re.search(r'<ColumnWidth[^>]*>\s*<!\[CDATA\[(.*?)\]\]>', cpt, re.S)
    assert len(widths.group(1).split(",")) == len(heads)
    # 表头行统一用样式 0
    assert set(re.findall(r'<C c="\d+" r="0" s="(\d+)"', cpt)) == {"0"}

    # 数据行样式：两个文本列用默认样式 1，金额与比率各引用一个格式样式，
    # 且引用的下标必须真的有定义，否则显示会花
    styles = re.findall(r'<Style style_name="([^"]+)"', cpt)
    data_s = [int(s) for s in re.findall(r'<C c="\d+" r="1" s="(\d+)"', cpt)]
    assert data_s == [1, 1, 2, 3], "数据行样式下标不对：%s" % data_s
    assert max(data_s) < len(styles), "样式下标越界：%s / %d 个样式" % (data_s, len(styles))
    assert styles[2:] == ["格式 #,##0", "格式 0.00%"], styles
    # <Format> 必须是 <Style> 的第一个子节点，排在 <FRFont> 之前
    for st in re.findall(r"<Style\b.*?</Style>", cpt, re.S)[2:]:
        assert re.match(r'<Style\b[^>]*>\s*<Format ', st), "Format 不是第一个子节点"
    fmt_nodes = [e.text.strip() for e in root.iter("Format")]
    assert fmt_nodes == ["#,##0", "0.00%"], fmt_nodes

    # --no-format 时退回两个样式，全部数据列用默认样式
    plain = build_cpt(sql, "TEST_CONN", heads, params, {}, {"query_month"},
                      DEFAULT_DATE_FORMAT, DEFAULT_DATE_VALUE, set(),
                      DEFAULT_STYLELIST, with_format=False)
    assert "CoreDecimalFormat" not in plain, "--no-format 仍写了格式"
    assert set(re.findall(r'<C c="\d+" r="1" s="(\d+)"', plain)) == {"1"}

    # 设计器不输出 textStyle，写了下次保存就丢
    assert "textStyle" not in cpt
    # CDATA 按设计器写法紧跟结束标签
    assert not re.search(r"\]\]>\s*\n\s*</\w+>", cpt), "CDATA 结束标签应紧跟"

    print("self-test OK：解析 %d 列 / %d 参数，控件 %d 个，样式 %d 个，XML 可解析"
          % (len(heads), len(params), len(ws), len(styles)))


# ---------------------------------------------------------------- 入口
def main():
    ap = argparse.ArgumentParser(
        description="从 SQL 生成帆软 .cpt 模板",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="SQL 的每个输出列都要显式写 AS 中文别名，帆软按列名绑定数据。")
    ap.add_argument("sql", nargs="?", help="输入 .sql 文件")
    ap.add_argument("-o", "--out", help="输出 .cpt 路径（默认与 sql 同名）")
    ap.add_argument("-c", "--connection", help="帆软数据连接名（必填）")
    ap.add_argument("--date-params", default="",
                    help="强制用日期控件的参数，逗号分隔")
    ap.add_argument("--text-params", default="",
                    help="强制用文本框的参数，逗号分隔")
    ap.add_argument("--labels", default="",
                     help="参数中文名，如 query_month=统计月份,record_no=记录编号")
    ap.add_argument("--unit", default=DEFAULT_UNIT, choices=sorted(UNIT_FORMAT),
                    help="金额列的单位口径，决定小数位，默认 %(default)s")
    ap.add_argument("--no-format", action="store_true",
                    help="不给数字列设格式（样式表只留表头与默认两个）")
    ap.add_argument("--optional", default="",
                    help="非必填参数，逗号分隔（默认全部必填）")
    ap.add_argument("--date-format", default=DEFAULT_DATE_FORMAT,
                    help="日期控件格式，默认 %(default)s")
    ap.add_argument("--default", default=DEFAULT_DATE_VALUE,
                    help="日期控件默认值，默认 %(default)s（上月）")
    ap.add_argument("--template",
                    help="跟随设计器保存过的 .cpt，以它的 StyleList 为准")
    ap.add_argument("--self-test", action="store_true", help="跑自检后退出")
    a = ap.parse_args()

    if a.self_test:
        self_test()
        return 0

    if not a.sql or not a.connection:
        ap.error("需要 sql 文件与 --connection（帆软数据连接名）")

    with open(a.sql, "r", encoding="utf-8") as f:
        sql = f.read().strip()

    headers = extract_headers(sql)
    params = extract_params(sql)

    def parse_list(s):
        return {x.strip() for x in s.split(",") if x.strip()}

    forced_date, forced_text = parse_list(a.date_params), parse_list(a.text_params)
    unknown = (forced_date | forced_text) - set(params)
    assert not unknown, "指定的参数不在 SQL 中：%s" % sorted(unknown)

    date_params = {p for p in params
                   if p in forced_date
                   or (p not in forced_text
                       and any(k in p.lower() for k in DATE_HINT))}

    labels = {}
    for pair in a.labels.split(","):
        if "=" in pair:
            k, v = pair.split("=", 1)
            labels[k.strip()] = v.strip()

    stylelist = DEFAULT_STYLELIST
    if a.template:
        with open(a.template, "r", encoding="utf-8") as f:
            m = re.search(r"<StyleList>.*?</StyleList>", f.read(), re.S)
        assert m, "%s 中未找到 StyleList" % a.template
        stylelist = m.group(0)

    cpt = build_cpt(sql, a.connection, headers, params, labels, date_params,
                    a.date_format, a.default, parse_list(a.optional), stylelist,
                    unit=a.unit, with_format=not a.no_format)
    ET.fromstring(cpt)   # 生成即验证：解析不过的文件在设计器里是空白模板

    out = a.out or os.path.splitext(a.sql)[0] + ".cpt"
    # 统一 LF，与设计器一致，避免混合换行符
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write(cpt)

    print("%s  %d 列 / %d 参数（日期控件：%s）"
          % (out, len(headers), len(params), sorted(date_params) or "无"))
    print("   总宽 %dpx，最宽列 %dpx"
          % (sum(col_px(h) for h in headers), max(col_px(h) for h in headers)))
    if not a.no_format:
        used = sorted({f for h in headers if (f := col_format(h, a.unit))})
        print("   数字格式（%s 口径）：%s" % (a.unit, "、".join(used) or "无"))
    print("   接着跑：python check_cpt.py %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
