from kb_gateway.chunker import chunk_markdown
from kb_gateway.parser import parse_markdown


def test_front_matter_is_separated() -> None:
    parsed = parse_markdown("---\ntitle: 示例\nversion: 1\n---\n\n# 正文\n内容\n")
    assert parsed.front_matter == {"title": "示例", "version": 1}
    assert parsed.body.startswith("\n# 正文")


def test_heading_path_and_code_block_kept() -> None:
    body = "# 总览\n\n## 接口\n\n```python\n# 这不是标题\nprint(1)\n```\n"
    chunks = chunk_markdown(body)
    assert chunks, "应至少产出一个分块"
    code = [chunk for chunk in chunks if chunk.kind == "code"]
    assert code and "# 这不是标题" in code[0].content
    assert code[0].heading_path == "总览 > 接口"


def test_large_table_repeats_header() -> None:
    header = "| 字段 | 类型 |"
    separator = "| --- | --- |"
    rows = [f"| field_{index} | string |" for index in range(25)]
    body = "# 表结构\n\n" + "\n".join([header, separator, *rows]) + "\n"
    chunks = chunk_markdown(body)
    assert len(chunks) >= 3
    for chunk in chunks:
        assert chunk.kind == "table"
        assert chunk.content.startswith(header)
        assert separator in chunk.content
        assert chunk.table_header == ["字段", "类型"]


def test_paragraph_window_overlap() -> None:
    body = "# 长文\n\n" + ("知识库网关" * 400) + "\n"
    chunks = chunk_markdown(body)
    assert len(chunks) > 1
    assert all(len(chunk.content) <= 512 * 3 for chunk in chunks)
