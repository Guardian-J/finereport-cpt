# -*- coding: utf-8 -*-
"""
把 .sql 文件同步进已有 .cpt 的 <Query> CDATA，其余部分一个字节不动。

为什么单独做这个：改 SQL 之后最省事的做法看起来是重新生成整个 cpt，但用户在
设计器里调过的东西（数字格式、条件属性、合并单元格、图表）会全部丢失。这个
脚本只换 SQL，顺带同步参数节点——因为 SQL 里的 ${xxx} 变了，参数不跟着改就
会出现「面板有控件但 SQL 取不到值」或者反过来。

用法：
    python sync_sql.py 报表.cpt 报表_SQL.sql
    python sync_sql.py reports/ --sql-dir reports  # 批量，按 报表名_SQL.sql 配对
    python sync_sql.py 报表.cpt 报表_SQL.sql --no-params   # 只换 SQL 不动参数

改完记得跑 check_cpt.py。参数变了的话面板控件不会自动跟着增删——脚本会提示，
需要用 gen_cpt.py 重建参数面板。

自检：python sync_sql.py --self-test
"""
import argparse
import glob
import os
import re
import sys
import xml.etree.ElementTree as ET

QUERY_RE = re.compile(r"(<Query>\s*<!\[CDATA\[)(.*?)(\]\]></Query>)", re.S)
# 兼容 ]]> 与结束标签之间有换行的旧写法
QUERY_LOOSE_RE = re.compile(r"(<Query>\s*<!\[CDATA\[)(.*?)(\]\]>\s*</Query>)", re.S)
PARAM_NODE = '<Parameter>\n<Attributes name="%s"/>\n<O>\n<![CDATA[]]></O>\n</Parameter>\n'


def sql_params(sql):
    """取 SQL 占位符，先去注释——头部参数说明里的 ${xxx} 不是真占位符"""
    body = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    body = re.sub(r"--[^\n]*", " ", body)
    return list(dict.fromkeys(re.findall(r"\$\{(\w+)\}", body)))


def replace_params(cpt, params):
    """重写数据集与报表两处参数节点。

    两处的包装不一样：数据集在 <Parameters>...</Parameters> 里，
    报表级直接裸放在 </ParameterUI> 之后、</ReportParameterAttr> 之前。
    """
    nodes = "".join(PARAM_NODE % p for p in params)

    # 数据集参数：<TableData> 里第一个 <Parameters>
    end = cpt.find("</TableDataMap>")
    assert end != -1, "未找到 TableDataMap"
    head, tail = cpt[:end], cpt[end:]
    m = re.search(r"<Parameters>.*?</Parameters>", head, re.S)
    assert m, "数据集缺少 <Parameters> 节点"
    head = head[: m.start()] + "<Parameters>\n" + nodes + "</Parameters>" + head[m.end():]
    cpt = head + tail

    # 报表级参数
    i, j = cpt.find("</ParameterUI>"), cpt.find("</ReportParameterAttr>")
    assert i != -1 and j != -1, "未找到 ParameterUI / ReportParameterAttr"
    i += len("</ParameterUI>")
    return cpt[:i] + "\n" + nodes + cpt[j:]


def sync(cpt_path, sql_path, sync_params=True):
    with open(sql_path, "r", encoding="utf-8") as f:
        sql = f.read().strip()
    assert "]]>" not in sql, "%s: SQL 含 ]]> 会截断 CDATA" % os.path.basename(sql_path)

    with open(cpt_path, "r", encoding="utf-8", newline="") as f:
        cpt = f.read()

    name = os.path.basename(cpt_path)[:-4]
    rx = QUERY_RE if QUERY_RE.search(cpt) else QUERY_LOOSE_RE
    hits = rx.findall(cpt)
    assert len(hits) == 1, "%s: 找到 %d 个 <Query>，预期 1 个" % (name, len(hits))

    old_params = sql_params(hits[0][1])
    new_params = sql_params(sql)

    # 用 lambda 避免 SQL 里的 \1 之类被当成反向引用
    cpt = rx.sub(lambda m: m.group(1) + sql + "]]></Query>", cpt, count=1)

    changed = old_params != new_params
    if changed and sync_params:
        cpt = replace_params(cpt, new_params)

    ET.fromstring(cpt)   # 同步即验证：坏掉的 XML 在设计器里是空白模板

    with open(cpt_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(cpt.replace("\r\n", "\n"))

    return name, old_params, new_params, changed and sync_params


def self_test():
    import tempfile
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import gen_cpt

    sql1 = "SELECT t.record_no AS 记录编号\nFROM fact_table t\nWHERE t.snapshot_month LIKE '${query_month}%'\n;"
    heads, params = gen_cpt.extract_headers(sql1), gen_cpt.extract_params(sql1)
    cpt = gen_cpt.build_cpt(sql1, "C", heads, params, {}, {"query_month"},
                            "yyyyMM", "=MONTHDELTA(TODAY(), -1)", set(),
                            gen_cpt.DEFAULT_STYLELIST)
    d = tempfile.mkdtemp()
    cp, sp = os.path.join(d, "t.cpt"), os.path.join(d, "t_SQL.sql")
    with open(cp, "w", encoding="utf-8", newline="\n") as f:
        f.write(cpt)

    # 换 SQL 且新增一个参数，注释里的 ${fake} 不该被当成参数
    sql2 = ("-- 参数：${fake} 只是注释里的说明\n"
            "SELECT t.record_no AS 记录编号, t.record_name AS 记录名称\nFROM fact_table t\n"
            "WHERE t.snapshot_month LIKE '${query_month}%' AND t.record_no = '${record_no}'\n;")
    with open(sp, "w", encoding="utf-8") as f:
        f.write(sql2)

    n, old, new, did = sync(cp, sp)
    assert old == ["query_month"], old
    assert new == ["query_month", "record_no"], new
    assert did, "参数变了却没同步"

    out = open(cp, encoding="utf-8").read()
    assert sql2 in out, "SQL 未写入"
    ds = out[: out.find("</TableDataMap>")]
    rp = out[out.find("</ParameterUI>"): out.find("</ReportParameterAttr>")]
    assert re.findall(r'<Attributes name="(\w+)"/>', ds) == new, "数据集参数未同步"
    assert re.findall(r'<Attributes name="(\w+)"/>', rp) == new, "报表参数未同步"
    # 单元格与样式不能被动到
    assert out.count("<C c=") == cpt.count("<C c="), "单元格被改动"
    assert "<StyleList>" in out and "textStyle" not in out

    # 幂等：同一份 SQL 再同步一次，内容不变
    before = open(cp, encoding="utf-8").read()
    sync(cp, sp)
    assert open(cp, encoding="utf-8").read() == before, "重复同步不幂等"

    print("self-test OK：SQL 与参数同步正确，单元格未受影响，重复执行幂等")


def main():
    ap = argparse.ArgumentParser(description="把 .sql 同步进已有 .cpt 的 <Query>")
    ap.add_argument("cpt", nargs="?", help=".cpt 文件或目录")
    ap.add_argument("sql", nargs="?", help=".sql 文件（cpt 为目录时用 --sql-dir）")
    ap.add_argument("--sql-dir", help="批量模式下 报表名_SQL.sql 所在目录")
    ap.add_argument("--no-params", action="store_true", help="只换 SQL，不同步参数节点")
    ap.add_argument("--exclude", default="", help="跳过的文件名，逗号分隔")
    ap.add_argument("--self-test", action="store_true", help="跑自检后退出")
    a = ap.parse_args()

    if a.self_test:
        self_test()
        return 0
    if not a.cpt:
        ap.error("需要指定 .cpt 文件或目录")

    pairs = []
    if os.path.isdir(a.cpt):
        assert a.sql_dir, "cpt 为目录时需要 --sql-dir"
        skip = {x.strip() for x in a.exclude.split(",") if x.strip()}
        for p in sorted(glob.glob(os.path.join(a.cpt, "*.cpt"))):
            if os.path.basename(p) in skip:
                continue
            s = os.path.join(a.sql_dir, os.path.basename(p)[:-4] + "_SQL.sql")
            if os.path.exists(s):
                pairs.append((p, s))
            else:
                print("跳过  %s（找不到 %s）" % (os.path.basename(p)[:-4],
                                              os.path.basename(s)))
    else:
        assert a.sql, "需要指定 .sql 文件"
        pairs.append((a.cpt, a.sql))
    assert pairs, "没有可同步的文件"

    need_panel = []
    for cp, sp in pairs:
        name, old, new, did = sync(cp, sp, not a.no_params)
        if old != new:
            print("%-28s SQL已同步  参数 %s -> %s%s"
                  % (name, old, new, "（已同步节点）" if did else "（未动节点）"))
            need_panel.append(name)
        else:
            print("%-28s SQL已同步" % name)

    if need_panel:
        print()
        print("注意：%s 的参数有变化，参数节点已更新但面板控件没有自动增删。"
              % "、".join(need_panel))
        print("      用 gen_cpt.py 重建参数面板，或在设计器里手工调整。")
    print()
    print("接着跑：python check_cpt.py <目录或文件>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
