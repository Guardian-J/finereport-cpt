# finereport-cpt

这是一个专门面向帆软 FineReport 报表开发的通用 skill，不限定于 Codex，可供不同 AI 助手和自动化工具使用，服务于 `.cpt` 报表模板的生成、修改、SQL 同步和交付校验。

它适用于使用帆软设计器或 FineReport 服务端维护报表模板的场景，不是通用 XML 编辑器，也不是与报表平台无关的通用报表工具。

它把 FineReport 模板中容易被设计器重写或导致空白模板的 XML 结构，整理成可复用的生成、同步和校验流程，适合从 SQL 快速生成基础报表，也适合在保留设计器调整的前提下更新已有模板。

## 能做什么

- 从带显式 `AS` 别名的 SQL 生成基础 `.cpt` 模板。
- 从 `${param}` 占位符生成数据集参数、报表级参数和查询面板控件。
- 自动生成日期控件、文本控件、必填校验、默认值、列宽和常用数字格式。
- 只同步已有模板的 SQL 和参数节点，保留单元格、合并、样式和条件属性。
- 静态检查 XML、数据连接、参数一致性、控件嵌套、样式索引、列宽和常见 SQL 问题。
- 提供 FineReport XML 节点参考，便于扩展到分组、动态表头、条件属性和其他高级结构。

## 目录

```text
finereport-cpt/
├── SKILL.md                  # AI 助手使用说明
├── README.md                 # 项目说明
├── references/
│   └── cpt-xml.md            # .cpt XML 结构参考
├── scripts/
│   ├── gen_cpt.py            # 从 SQL 生成模板
│   ├── sync_sql.py            # 将 SQL 同步进已有模板
│   └── check_cpt.py           # 交付前静态校验
└── evals/
    └── evals.json            # 使用场景评测样例
```

## 环境

- 目标平台为帆软 FineReport，主要面向 `.cpt` 报表模板开发。
- Python 3.9 或更高版本。
- FineReport 11.x 或与目标模板兼容的版本。
- 帆软服务端已经配置好 SQL 使用的数据连接。
- 本项目不连接数据库，也不替用户配置 FineReport 服务端连接；脚本只处理模板 XML 和 SQL 文本。

## 快速开始

### 生成新模板

SQL 的每个输出列都写显式 `AS` 别名，模板会按别名生成表头和数据绑定：

```sql
SELECT
    t.record_no AS 记录编号,
    t.record_name AS 记录名称,
    t.amount AS 金额
FROM fact_table t
WHERE t.snapshot_month LIKE '${query_month}%'
  AND t.record_no LIKE '%${record_no}%';
```

```bash
python scripts/gen_cpt.py report.sql \
  -o report.cpt \
  --connection YOUR_CONNECTION \
  --date-params query_month \
  --optional record_no
```

`--connection` 必须填写目标 FineReport 环境中的真实连接名。`YOUR_CONNECTION`、`fact_table` 和示例字段只是占位符，不能直接当作生产配置。

### 更新已有模板的 SQL

设计器已经调整过的模板不要重新生成。使用同步脚本只替换 `<Query>` 和参数节点：

```bash
python scripts/sync_sql.py report.cpt report.sql
```

如果 SQL 参数发生增删，脚本会同步参数声明，但不会自动增删参数面板控件。此时需要重新生成参数面板或在设计器中调整控件。

### 交付前校验

```bash
python scripts/check_cpt.py report.cpt --connection YOUR_CONNECTION
```

批量校验并检查同名 SQL 文件：

```bash
python scripts/check_cpt.py reports/ \
  --connection YOUR_CONNECTION \
  --sql-dir reports/
```

三个脚本都提供内置自检：

```bash
python scripts/gen_cpt.py --self-test
python scripts/sync_sql.py --self-test
python scripts/check_cpt.py --self-test
```

## 设计原则

1. 以 FineReport 设计器保存的模板为格式基准，不凭直觉拼装 Java 对象对应的 XML。
2. 新模板走 `SQL -> gen_cpt.py -> check_cpt.py`。
3. 已有模板改 SQL 走 `sync_sql.py`，避免覆盖设计器中的布局和样式。
4. 参数必须同时存在于数据集和报表级定义中；参数面板控件必须包在 `BoundsWidget` 中。
5. 表头不依赖 `textStyle="1"` 防止换行，优先保证列宽足够。
6. 样式表按位置索引引用，新增样式只追加，不能随意删除或插入中间样式。
7. 静态校验通过不等于数据库查询通过，最终仍需在目标 FineReport 设计器或运行环境中预览验证。

## 项目隔离

本仓库只提供通用 FineReport 模板能力，不包含任何特定公司的连接名、数据库、表名、字段口径、报表目录或业务规则。使用时应从目标项目的说明文档、DDL、现有设计器模板和用户需求中取得这些信息。

## 版本说明

当前 XML 结构和常量主要按 FineReport 11.x 设计器产物整理。不同版本可能重写节点顺序、字体、主题颜色或控件属性；遇到差异时，以目标版本设计器实际保存的模板为准。

## 许可

本项目采用 MIT License，详见 [LICENSE](LICENSE)。
