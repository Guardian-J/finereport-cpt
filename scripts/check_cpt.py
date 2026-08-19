# -*- coding: utf-8 -*-
"""
帆软 .cpt 交付自检：断言式，任何一条不过就抛 AssertionError 并指出具体问题。

为什么需要它：.cpt 写坏了设计器不一定报错，而是静默降级成空白模板，或者
参数面板不弹、必填校验不生效——这些都要打开设计器才发现。这个脚本把这些
坑变成可自动化的断言。

用法：
    python check_cpt.py 报表.cpt                    # 单个
    python check_cpt.py reports/*.cpt               # 批量
    python check_cpt.py reports/ --connection MY_DB   # 指定目录并校验连接名
    python check_cpt.py reports/ --sql-dir reports --exclude sample.cpt

自检：python check_cpt.py --self-test
"""
import argparse
import glob
import os
import re
import sys
import xml.etree.ElementTree as ET

UNIT_PER_PX = 34290
BOUNDS_WIDGET = "com.fr.form.ui.container.WAbsoluteLayout$BoundsWidget"
INPUT_CLASSES = ("com.fr.form.ui.TextEditor", "com.fr.form.ui.DateEditor",
                 "com.fr.form.ui.ComboBox", "com.fr.form.ui.NumberEditor",
                 "com.fr.form.ui.ComboCheckBox", "com.fr.form.ui.TreeEditor")
CHAR_PX, PADDING_PX = 16, 20


def header_need_px(text):
    """表头文字所需宽度，与 gen_cpt.py 同一口径"""
    return int(sum(CHAR_PX if ord(c) > 127 else CHAR_PX * 0.55 for c in text)) + PADDING_PX


def check(path, connection=None, sql_dir=None, strict_width=True):
    """校验单个 .cpt，返回摘要 dict。任何问题抛 AssertionError。"""
    name = os.path.basename(path)[:-4]
    with open(path, "r", encoding="utf-8", newline="") as f:
        text = f.read()

    # 1. XML 必须可解析。解析不过的文件设计器会降级成空白模板，
    #    而且不会明确告诉你是哪一行坏了，所以先在这里挡住。
    root = ET.fromstring(text)

    # 2. 数据连接：必须是真实连接，不能残留帆软自带示例连接
    dbs = [e.text.strip() for e in root.iter("DatabaseName") if e.text]
    assert dbs, "%s: 未找到 DatabaseName（数据集没配数据连接）" % name
    for db in dbs:
        assert not db.startswith("FRDemo"), \
            "%s: 数据连接为示例连接 %s，会报「驱动器未找到」" % (name, db)
        if connection:
            assert db == connection, "%s: 数据连接为 %s，应为 %s" % (name, db, connection)

    # 3. 内嵌 SQL 与同名 .sql 文件一致（防止改了 SQL 忘了同步进模板）
    queries = [e.text for e in root.iter("Query") if e.text]
    assert queries, "%s: 数据集没有 SQL" % name
    query = queries[0]
    if sql_dir:
        sql_file = os.path.join(sql_dir, name + "_SQL.sql")
        if os.path.exists(sql_file):
            with open(sql_file, "r", encoding="utf-8") as f:
                assert query.strip() == f.read().strip(), \
                    "%s: 内嵌 SQL 与 %s 不一致，需重新同步" % (name, os.path.basename(sql_file))

    # 4. 参数三方一致：SQL 占位符 == 数据集参数 == 报表参数
    #    用有序列表而非集合来比：集合会把重复的 <Parameter> 节点吃掉，
    #    而重复节点在设计器里会显示成两个同名参数，集合比较看不出来。
    sql_body = re.sub(r"/\*.*?\*/", " ", query, flags=re.S)
    sql_body = re.sub(r"--[^\n]*", " ", sql_body)
    sql_params = list(dict.fromkeys(re.findall(r"\$\{(\w+)\}", sql_body)))

    ds_seg = text[: text.find("</TableDataMap>")]
    ds_params = re.findall(r'<Attributes name="(\w+)"/>', ds_seg)
    # 报表级参数在 </ParameterUI> 之后、</ReportParameterAttr> 之前，无 <Parameters> 包装
    i, j = text.find("</ParameterUI>"), text.find("</ReportParameterAttr>")
    assert i != -1 and j != -1, "%s: 缺少 ParameterUI / ReportParameterAttr" % name
    rp_params = re.findall(r'<Attributes name="(\w+)"/>', text[i:j])

    assert len(set(ds_params)) == len(ds_params), "%s: 数据集参数重复 %s" % (name, ds_params)
    assert len(set(rp_params)) == len(rp_params), "%s: 报表参数重复 %s" % (name, rp_params)
    assert ds_params == sql_params, \
        "%s: 数据集参数 %s 与 SQL 占位符 %s 不一致（SQL 取不到值）" % (name, ds_params, sql_params)
    assert rp_params == sql_params, \
        "%s: 报表参数 %s 与 SQL 占位符 %s 不一致（面板不弹或漏控件）" % (name, rp_params, sql_params)

    # 5. 参数面板控件嵌套。WParameterLayout 继承 WAbsoluteLayout，它的每个
    #    Widget 子节点都会被强转成 BoundsWidget；直接挂真实控件会抛
    #    ClassCastException，设计器把模板降级成空白 WorkBook。
    layout = next((e for e in root.iter("Layout")
                   if e.get("class", "").endswith("WParameterLayout")), None)
    widgets = []
    if sql_params:
        assert layout is not None, "%s: 有参数但缺少 WParameterLayout（面板不会弹）" % name
        widgets = layout.findall("Widget")
        assert widgets, "%s: 参数面板里没有控件" % name
        for w in widgets:
            assert w.get("class") == BOUNDS_WIDGET, \
                "%s: 控件外层是 %s，应为 BoundsWidget，否则设计器打开是空白" % (name, w.get("class"))
            inner = w.find("InnerWidget")
            assert inner is not None, "%s: BoundsWidget 缺少 InnerWidget" % name
            assert w.find("BoundsAttr") is not None, "%s: BoundsWidget 缺少 BoundsAttr" % name

        # 每个参数一个输入控件，且顺序与参数一致
        editors = [w.find("InnerWidget").find("WidgetName").get("name")
                   for w in widgets
                   if w.find("InnerWidget").get("class") in INPUT_CLASSES]
        assert editors == rp_params, \
            "%s: 输入控件 %s 与报表参数 %s 不匹配" % (name, editors, rp_params)
        assert any(w.find("InnerWidget").get("class", "").endswith("FormSubmitButton")
                   for w in widgets), "%s: 缺少查询按钮，用户改了参数没法提交" % name

        # 控件不能重叠，否则面板上互相压住
        bounds = sorted((int(w.find("BoundsAttr").get("x")),
                         int(w.find("BoundsAttr").get("width")),
                         w.find("InnerWidget").find("WidgetName").get("name"))
                        for w in widgets)
        for (x1, w1, n1), (x2, _, n2) in zip(bounds, bounds[1:]):
            assert x1 + w1 <= x2, "%s: 控件 %s 与 %s 重叠" % (name, n1, n2)

        # 必填校验必须是独立子节点。写成 <DateAttr allowBlank="false"/> 属性
        # 不报错，但校验完全不生效——属于最难发现的错。
        for w in widgets:
            inner = w.find("InnerWidget")
            if inner.get("class") not in INPUT_CLASSES:
                continue
            for attr in ("DateAttr", "TextAttr", "NumberAttr"):
                node = inner.find(attr)
                assert node is None or node.get("allowBlank") is None, \
                    "%s: %s 的 allowBlank 写成了 %s 的属性，应为独立子节点 <allowBlank>" % (
                        name, inner.find("WidgetName").get("name"), attr)

    # 6. 样式：引用的样式索引都要有定义，同一行样式统一
    #    样式数量不固定——带数字格式的报表会在表头/默认之外多出格式样式，
    #    所以这里只校验下标有效，不写死样式个数。
    style_blocks = re.findall(r"<Style\b.*?</Style>", text, re.S)
    styles = re.findall(r'<Style style_name="([^"]+)"', text)
    cells = re.findall(r'<C c="(\d+)" r="(\d+)" s="(\d+)"', text)
    assert cells, "%s: 没有单元格" % name
    if style_blocks:
        for _, _, s in cells:
            assert int(s) < len(style_blocks), \
                "%s: 单元格引用样式 %s，但只定义了 %d 个样式" % (name, s, len(style_blocks))
    row0 = {s for _, r, s in cells if r == "0"}
    assert len(row0) <= 1, "%s: 表头行样式不统一 %s，显示会花" % (name, sorted(row0))

    # 数字格式节点必须是 <Style> 的第一个子节点（在 <FRFont> 之前），
    # 放到后面帆软读不到，单元格看着就是没设格式
    for st in style_blocks:
        if "CoreDecimalFormat" in st:
            assert re.match(r'<Style\b[^>]*>\s*<Format ', st), \
                "%s: <Format> 不是 <Style> 的第一个子节点，数字格式不生效" % name
    fmt_nodes = [e.text.strip() for e in root.iter("Format") if e.text]
    for f in fmt_nodes:
        assert f, "%s: 有空的 <Format> 节点" % name

    # 设计器保存时不输出 textStyle，写了也会丢，所以别依赖它防换行
    assert "textStyle" not in text, \
        "%s: 含 textStyle，设计器保存后会丢失；表头不换行应靠加宽列宽" % name

    # 7. 列宽必须放得下完整表头，否则表头文字换行或被截断
    cw = re.search(r'<ColumnWidth defaultValue="\d+">\s*<!\[CDATA\[(.*?)\]\]>', text, re.S)
    assert cw, "%s: 未找到 ColumnWidth" % name
    widths = [int(x) // UNIT_PER_PX for x in cw.group(1).split(",")]
    heads = {int(m.group(1)): m.group(2) for m in re.finditer(
        r'<C c="(\d+)" r="0"[^>]*>\s*<O>\s*<!\[CDATA\[(.*?)\]\]>', text, re.S)}
    narrow = []
    for i, h in sorted(heads.items()):
        assert i < len(widths), "%s: 第%d列有表头但没有列宽定义" % (name, i)
        need = header_need_px(h)
        if widths[i] < need:
            narrow.append("列%d「%s」%dpx<%dpx" % (i, h, widths[i], need))
    if strict_width:
        assert not narrow, "%s: 列宽放不下表头，会换行：%s" % (name, "; ".join(narrow))

    # 8. SQL 常见问题
    assert "AS AS" not in query, "%s: SQL 里有 AS AS" % name
    assert not re.search(r"divide\(\s*,", query), "%s: SQL 里有空 divide(" % name
    # JOIN/WHERE 里裸写 ${param}：参数为空时会拼出 AND  BETWEEN 之类的语法错
    for m in re.finditer(r"(?<!')\$\{(\w+)\}(?!')", query):
        ctx = query[max(0, m.start() - 40): m.end() + 10]
        assert "'" in ctx or "%" in ctx, \
            "%s: ${%s} 没加引号，参数为空时 SQL 语法报错，建议 COALESCE(NULLIF('${%s}',''), 兜底)" % (
                name, m.group(1), m.group(1))

    return dict(name=name, params=rp_params, widgets=len(widgets),
                cols=len(heads), widest=max(widths) if widths else 0,
                narrow=narrow, connection=dbs[0], styles=len(styles),
                formats=sorted(set(fmt_nodes)))


def self_test():
    """用临时文件验证：好文件通过，各类坏文件都能被抓住"""
    import tempfile
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import gen_cpt

    sql = ("SELECT t.record_no AS 记录编号, t.amount AS 金额, t.rate AS 完成率\n"
           "FROM fact_table t\n"
           "WHERE t.snapshot_month LIKE concat('${query_month}', '%')\n;")
    heads, params = gen_cpt.extract_headers(sql), gen_cpt.extract_params(sql)
    good = gen_cpt.build_cpt(sql, "MY_DB", heads, params, {"query_month": "统计月份"},
                            {"query_month"}, "yyyyMM", "=MONTHDELTA(TODAY(), -1)",
                            set(), gen_cpt.DEFAULT_STYLELIST, unit="万元")

    d = tempfile.mkdtemp()

    def write(text, fn="t.cpt"):
        p = os.path.join(d, fn)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        return p

    r = check(write(good), connection="MY_DB")
    assert r["params"] == ["query_month"] and r["widgets"] == 3, r
    assert not r["narrow"], r
    # 数字格式要被识别出来：金额千分位 + 比率百分比
    assert r["formats"] == ["#,##0", "0.00%"], r["formats"]

    # 每种坏法都必须被抓住，否则这个校验器就是摆设
    cases = [
        ("BoundsWidget 被拆掉",
         good.replace('<Widget class="%s">' % BOUNDS_WIDGET, "<Widget>", 1)
             .replace("</InnerWidget>\n<BoundsAttr", "</InnerWidget><!--x--><BoundsAttr", 1)),
        ("残留示例连接", good.replace("MY_DB", "FRDemo Database")),
        ("报表参数被删", good[: good.find("</ParameterUI>")]
            + good[good.find("</ParameterUI>"): good.find("</ReportParameterAttr>")]
            .replace('<Attributes name="query_month"/>', '<Attributes name="other"/>')
            + good[good.find("</ReportParameterAttr>"):]),
        ("allowBlank 写成属性",
         good.replace("<allowBlank>\n<![CDATA[false]]></allowBlank>\n", "")
             .replace('<DateAttr format="yyyyMM"/>',
                      '<DateAttr format="yyyyMM" allowBlank="false"/>')),
        ("残留 textStyle",
         good.replace('<Style style_name="表头"', '<Style style_name="表头" textStyle="1"')),
        ("列宽不够放表头",
         re.sub(r'(<ColumnWidth defaultValue="\d+">\s*<!\[CDATA\[)[^\]]*',
                lambda m: m.group(1) + ",".join([str(70 * UNIT_PER_PX)] * 3), good)),
        ("样式下标越界（增删样式没同步改单元格 s=）",
         good.replace('r="1" s="2"', 'r="1" s="99"', 1)),
        ("Format 不是第一个子节点",
         re.sub(r'(<Style\b[^>]*>)\s*(<Format class="com.fr.base.CoreDecimalFormat">\s*'
                r'<!\[CDATA\[[^\]]*\]\]></Format>)',
                lambda m: m.group(1) + "\n<Background name=\"NullBackground\"/>\n"
                + m.group(2), good, count=1)),
        ("参数节点重复",
         good.replace("</ParameterUI>\n<Parameter>\n<Attributes name=\"query_month\"/>",
                      "</ParameterUI>\n<Parameter>\n<Attributes name=\"query_month\"/>\n"
                      "<O>\n<![CDATA[]]></O>\n</Parameter>\n<Parameter>\n"
                      "<Attributes name=\"query_month\"/>")),
        ("SQL 参数没加引号",
         good.replace("concat('${query_month}', '%')", "${query_month}")),
    ]
    for label, bad in cases:
        try:
            check(write(bad, "bad.cpt"), connection="MY_DB")
        except (AssertionError, ET.ParseError):
            continue
        raise AssertionError("自检失败：「%s」这种坏法没被抓住" % label)

    print("self-test OK：好文件通过，%d 种坏法全部拦下" % len(cases))


def main():
    ap = argparse.ArgumentParser(description="帆软 .cpt 交付自检")
    ap.add_argument("paths", nargs="*", help=".cpt 文件或目录")
    ap.add_argument("-c", "--connection", help="期望的数据连接名")
    ap.add_argument("--sql-dir", help="存放 报表名_SQL.sql 的目录，用于比对内嵌 SQL")
    ap.add_argument("--exclude", default="", help="跳过的文件名，逗号分隔")
    ap.add_argument("--no-width-check", action="store_true",
                    help="列宽不足只警告不失败")
    ap.add_argument("--self-test", action="store_true", help="跑自检后退出")
    a = ap.parse_args()

    if a.self_test:
        self_test()
        return 0
    if not a.paths:
        ap.error("需要指定 .cpt 文件或目录")

    skip = {x.strip() for x in a.exclude.split(",") if x.strip()}
    files = []
    for p in a.paths:
        files.extend(sorted(glob.glob(os.path.join(p, "*.cpt"))) if os.path.isdir(p) else [p])
    files = [f for f in files if os.path.basename(f) not in skip]
    assert files, "没有要检查的 .cpt"

    failed = []
    for p in files:
        try:
            r = check(p, a.connection, a.sql_dir, strict_width=not a.no_width_check)
        except (AssertionError, ET.ParseError) as e:
            failed.append((os.path.basename(p), e))
            print("FAIL  %s" % e)
            continue
        warn = "  ⚠ 列宽偏窄: %s" % "; ".join(r["narrow"]) if r["narrow"] else ""
        print("OK    %-28s %2d列  控件%d个  参数=%s%s"
              % (r["name"], r["cols"], r["widgets"], r["params"], warn))

    print()
    if failed:
        print("%d/%d 未通过" % (len(failed), len(files)))
        return 1
    print("OK  %d/%d 全部通过" % (len(files), len(files)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
