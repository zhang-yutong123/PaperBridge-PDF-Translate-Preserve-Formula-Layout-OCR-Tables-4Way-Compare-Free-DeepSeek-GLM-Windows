"""Additional PDF reading formats for the desktop app."""

from pathlib import Path

import pymupdf


def make_side_by_side(dual_path: str) -> str:
    """Pair alternating original/translated pages into searchable spreads."""
    source_path = Path(dual_path)
    output_path = source_path.with_name(
        source_path.stem.removesuffix("-dual") + "-side-by-side.pdf"
    )
    with pymupdf.open(source_path) as source, pymupdf.open() as output:
        placements = []
        for index in range(0, len(source), 2):
            left = source[index].rect
            right = source[index + 1].rect
            height = max(left.height, right.height)
            page = output.new_page(width=left.width + right.width, height=height)
            left_y = (height - left.height) / 2
            right_y = (height - right.height) / 2
            for number, target in (
                (index, pymupdf.Rect(0, left_y, left.width, left_y + left.height)),
                (index + 1, pymupdf.Rect(left.width, right_y, left.width + right.width, right_y + right.height)),
            ):
                original = source[number]
                rotation = original.rotation
                links = original.get_links()
                placements.append((target, links))
                # show_pdf_page needs explicit rotation; clearing /Rotate first
                # also keeps cropped pages in the correct source coordinates.
                original.set_rotation(0)
                page.show_pdf_page(target, source, number, rotate=-rotation)
                original.set_rotation(rotation)

        # All destination pages must exist before adding internal page links.
        for number, (target, links) in enumerate(placements):
            offset = pymupdf.Matrix(1, 0, 0, 1, target.x0, target.y0)
            for link in links:
                if link['kind'] not in (pymupdf.LINK_URI, pymupdf.LINK_GOTO):
                    continue
                area = (link['from'] * offset) & target
                if area.is_empty:
                    continue
                copied = {'kind': link['kind'], 'from': area}
                if link['kind'] == pymupdf.LINK_URI:
                    copied['uri'] = link['uri']
                else:
                    destination = link['page']
                    if destination < 0:
                        continue
                    destination_rect = placements[destination][0]
                    copied.update(page=destination // 2,
                                  to=link['to'] + destination_rect.tl,
                                  zoom=link.get('zoom', 0))
                output[number // 2].insert_link(copied)
        output.save(output_path, garbage=3, deflate=True)
    return str(output_path)
