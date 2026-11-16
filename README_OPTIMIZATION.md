# 本次优化说明

公开接口与评分模型未变；只改了私有数据组织、内部更正交接和查询策略。

## 不变量

1. **分数与排序**：每词贡献 `Fraction(N*tf, df*(tf+L))`，逐词求和；排序键
   `(分数降序, ID升序)`。`query` 只按此键排序候选，从未改写分数。
2. **当前语料一致**：`Corpus` 先更新 `_rows`/`_df`/`version`，再向监听者发布
   `(id, 旧行, 新行)`。`Search` 在发布回调里只改该 id 涉及的词项，因此权重
   `N/df` 永远按发布时的当前 `N`、`df` 读取。空文档计入 `N`（`_rows` 拥有它），
   重复词只算一次 `df`、保留 `tf`。
3. **删除无残留**：某词最后一份支持文档被删时，该词的倒排表整个移除；命中查询
   按当前 `df==0` 自然返回 `[]`。
4. **局部交接**：一次更正只移动 1 个 id 在受影响词项上的 posting，并只更新这些
   词项的 `df` 与比值上界；不遍历语料、不重建、不比对发现变更。
5. **剪枝安全**：对未读词项 `t`，未读文档分数上界为
   `sum(weight(t) * max_ratio(t))`。`max_ratio` 是 `tf/(tf+L)` 在当前语料中的
   **精确最大值**：插入时单调抬高；若最大值持有者被删/替换，则**只对该词项**
   在本地重算（`_Term.tighten`）。它永远不会低估，故
   `第k名精确分数 > 上界` 时未读文档必定出局，含同分时靠 ID 的键比较也成立。

## 查询如何停止

- 按 `df` 升序读倒排表，读到的新文档立即用**全部**查询词算精确分数（字典查找），
  所以每个候选都是完整分数。
- 每读完一张表，从上界里减去它的贡献；当 `k` 个候选已就绪且
  `kth > 剩余上界` 立即停止。此时结果即精确答案（`last_degraded is None`）。
- 仅当上界仍无法排除未读表时才继续读表，`last_degraded` 记录原因：
  - `fewer_than_k_candidates`：精确候选不足 k 个，必须继续读；
  - `bound_inconclusive`：上界仍高于第 k 名，必须继续读。
  两种都是"当前安全界无法排除"的真实退化，不是固定全扫。所有负载上最多读到
  2 张表（`mixed` 为 1 张）。

`last_scored` 真实等于本次查询完整计分的文档数（候选并集大小），未硬编码。

## 更正交接的触发依据

`Corpus._publish` 只在成功提交后调用：非法 `replace/delete` 在 `valid_id` /
`counts` 处抛 `ValueError`，尚未发布，语料、`version`、索引与结果都不变（见
`verify.invalid`）。`Corpus(documents=...)` 通过 `replace` 构建，因此构建期间
每次更正也会实时交给已就绪的索引；`Search` 在语料已有内容时创建会先取一次
快照（`_attach` → `_rebuild`），之后只走局部交接。

## 核验与实测

- `python3 checks.py baseline` → `REGRESSION_OK`（原断言原样保留）。
- `python3 checks.py target` → 先跑 `verify.py` 的独立差分/边界/交接/非法输入/
  上界可靠性，再跑 `bench.main(True)`，成功输出 `TARGET_OK`。
- `python3 checks.py demo` → 真实更正前后排名（df 下降使未编辑文档升降分、
  `last_degraded`、`last_lists_read`、长度替换）。
- `python3 bench.py baseline|target` 可重跑；每类 60000 文档、每轮 10 次更正 +
  40 次查询、三轮取中位数，与同进程冻结逐文档参照比较。当前中位比值约
  `0.0064`（mixed）与 `0.0073`（df_jump），构建/执行分配峰值各约 57 MiB
  （< 96 MiB）。构建与执行分别计时与计量。逐负载 `degradation_reasons` 记录
  每次查询的停止依据。

`verify.py` 覆盖：随机更正/查询对冻结参照与 `checks.oracle` 的差分；未知词、
最后支持者删除、长度替换、空文档、同分截断（k 切在并列组中间、跨倒排表）、
负/极大 ID、空语料、`k>N`；删除最大值持有者后上界立即收紧；每次编辑后索引与
"从当前语料全量重建"逐词相等；非法输入整次拒绝。它不修改 `reference.py`、
`samples.py`、`bench.py` 的原有断言。
