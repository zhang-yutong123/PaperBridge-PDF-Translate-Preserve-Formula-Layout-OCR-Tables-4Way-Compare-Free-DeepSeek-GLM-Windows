"""Page selection shared by the desktop interface and command-line adapter."""


def parse_page_spec(spec, page_count=None):
    """Return zero-based page numbers; an explicit range must select a page."""
    spec = str(spec or "").strip().translate(str.maketrans("，－–—", ",---"))
    if not spec:
        raise ValueError("请填写要翻译的页码，例如 1-5,8,10。")

    selected = set()
    for item in spec.split(","):
        parts = item.strip().split("-")
        if len(parts) not in (1, 2) or any(not part.strip().isdecimal() for part in parts):
            raise ValueError("页码格式不正确，请使用 1-5,8,10 这样的格式。")
        start = int(parts[0])
        end = int(parts[-1])
        if start < 1 or end < 1:
            raise ValueError("页码从 1 开始，不能填写 0。")
        if start > end:
            raise ValueError(f"页码范围 {start}-{end} 的起始页不能大于结束页。")
        if page_count is not None and end > page_count:
            raise ValueError(f"这份 PDF 只有 {page_count} 页，不能选择第 {end} 页。")
        selected.update(range(start - 1, end))
    return sorted(selected)


def select_pages(choice, spec, page_count):
    """Match the GUI's four choices, retaining None exclusively for all pages."""
    if choice == "All":
        return None
    if choice == "First":
        return [0]
    if choice == "First 5 pages":
        return list(range(min(5, page_count)))
    return parse_page_spec(spec, page_count)
