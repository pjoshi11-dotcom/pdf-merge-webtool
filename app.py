import sys
from io import BytesIO
from typing import List, Dict, Any

import streamlit as st
from pypdf import PdfReader, PdfWriter

# Keep fixed to avoid Streamlit Cloud OverflowError
sys.setrecursionlimit(5000)

st.set_page_config(page_title="PDF Merge & Split Tool", layout="wide")


def safe_filename(name: str) -> str:
    invalid = '<>:"/\\|?*'
    cleaned = ''.join('_' if c in invalid else c for c in name).strip()
    return cleaned[:120] or 'Output'


def flatten_outline(outline, reader, level=1, result=None):
    if result is None:
        result = []
    for item in outline:
        if isinstance(item, list):
            flatten_outline(item, reader, level + 1, result)
        else:
            title = None
            try:
                title = getattr(item, 'title', None) or item.get('/Title')
            except Exception:
                title = str(item)
            try:
                page_num = reader.get_destination_page_number(item)
            except Exception:
                page_num = None
            if title is not None and page_num is not None and page_num >= 0:
                result.append({
                    'title': str(title),
                    'page': int(page_num),
                    'level': int(level),
                })
    return result


def subset_bookmarks(bookmarks: List[Dict[str, Any]], start_page: int, end_page: int):
    subset = [bm.copy() for bm in bookmarks if start_page <= bm['page'] < end_page]
    if not subset:
        return []
    min_level = min(bm['level'] for bm in subset)
    for bm in subset:
        bm['adj_page'] = bm['page'] - start_page
        bm['adj_level'] = bm['level'] - min_level + 1
    return subset


def add_bookmarks_to_writer(writer: PdfWriter, bookmarks_subset: List[Dict[str, Any]]):
    if not bookmarks_subset:
        return
    parents_by_level = {}
    for bm in bookmarks_subset:
        level = bm['adj_level']
        parent = parents_by_level.get(level - 1)
        ref = writer.add_outline_item(title=bm['title'], page_number=bm['adj_page'], parent=parent)
        parents_by_level[level] = ref
        stale = [k for k in parents_by_level if k > level]
        for k in stale:
            parents_by_level.pop(k, None)


def merge_files_with_bookmarks(sorted_files):
    writer = PdfWriter()
    try:
        for _, up_file in sorted_files:
            up_file.seek(0)
            writer.append(up_file, import_outline=True)
        output = BytesIO()
        writer.write(output)
        output.seek(0)
        return output
    finally:
        try:
            writer.close()
        except Exception:
            pass


def split_into_two_parts(pdf_bytes: bytes, bookmarks: List[Dict[str, Any]], selected_idx: int):
    reader = PdfReader(BytesIO(pdf_bytes))
    total_pages = len(reader.pages)
    selected_bm = bookmarks[selected_idx]
    split_page = selected_bm['page']

    if split_page <= 0 or split_page >= total_pages:
        raise ValueError("Selected bookmark must be after page 1 and before the last page to create Part 1 and Part 2.")

    part1_writer = PdfWriter()
    for page_no in range(0, split_page):
        part1_writer.add_page(reader.pages[page_no])
    add_bookmarks_to_writer(part1_writer, subset_bookmarks(bookmarks, 0, split_page))
    part1_output = BytesIO()
    part1_writer.write(part1_output)
    part1_output.seek(0)
    try:
        part1_writer.close()
    except Exception:
        pass

    part2_writer = PdfWriter()
    for page_no in range(split_page, total_pages):
        part2_writer.add_page(reader.pages[page_no])
    add_bookmarks_to_writer(part2_writer, subset_bookmarks(bookmarks, split_page, total_pages))
    part2_output = BytesIO()
    part2_writer.write(part2_output)
    part2_output.seek(0)
    try:
        part2_writer.close()
    except Exception:
        pass

    return part1_output, part2_output, selected_bm, split_page, total_pages


st.title("PDF Merge & Split Tool")
st.caption("Merge PDFs with bookmarks preserved, or split one MRB PDF into Part 1 and Part 2 based on a selected bookmark.")

merge_tab, split_tab = st.tabs(["Merge PDFs", "Split by Bookmark"])

with merge_tab:
    st.subheader("Merge PDFs")
    uploaded_files = st.file_uploader(
        "Upload PDF files for merge",
        type=["pdf"],
        accept_multiple_files=True,
        key="merge_uploader"
    )

    output_merge_name = st.text_input("Merged output file name", value="Merged_Output", key="merge_name")

    if uploaded_files:
        st.markdown("### Set merge sequence")
        file_orders = []
        used = []
        cols = st.columns([5, 2])
        cols[0].markdown("**File**")
        cols[1].markdown("**Sequence**")

        for i, file in enumerate(uploaded_files, start=1):
            row = st.columns([5, 2])
            row[0].write(file.name)
            seq = row[1].number_input(
                f"Sequence for {file.name}",
                min_value=1,
                max_value=len(uploaded_files),
                value=i,
                step=1,
                key=f"merge_seq_{file.name}_{i}",
                label_visibility="collapsed"
            )
            used.append(seq)
            file_orders.append((seq, file))

        if len(set(used)) != len(used):
            st.error("Sequence numbers must be unique.")
        elif st.button("Merge PDFs", type="primary"):
            try:
                sorted_files = sorted(file_orders, key=lambda x: x[0])
                merged_output = merge_files_with_bookmarks(sorted_files)
                st.success("PDF merged successfully with bookmarks preserved where available.")
                st.download_button(
                    label="Download Merged PDF",
                    data=merged_output,
                    file_name=f"{safe_filename(output_merge_name)}.pdf",
                    mime="application/pdf"
                )
            except Exception as e:
                st.error(f"Error while merging PDFs: {e}")

with split_tab:
    st.subheader("Split by Bookmark")
    split_file = st.file_uploader(
        "Upload one MRB PDF for split",
        type=["pdf"],
        accept_multiple_files=False,
        key="split_uploader"
    )
    output_base_name = st.text_input("Output base name", value="Split_Output", key="split_name")

    if split_file is not None:
        pdf_bytes = split_file.getvalue()

        col_a, col_b = st.columns([1, 4])
        load_clicked = col_a.button("Load Bookmarks")
        col_b.caption("Selected bookmark will become the start of Part 2. Part 1 will contain everything before it.")

        if load_clicked:
            try:
                reader = PdfReader(BytesIO(pdf_bytes))
                outline = getattr(reader, 'outline', None)
                if outline is None:
                    st.session_state["split_bookmarks"] = []
                    st.warning("No bookmarks/outlines found in this PDF.")
                else:
                    bookmarks = flatten_outline(outline, reader)
                    st.session_state["split_bookmarks"] = bookmarks
                    st.session_state["split_pdf_bytes"] = pdf_bytes
                    st.success(f"Loaded {len(bookmarks)} bookmark(s).")
            except Exception as e:
                st.session_state["split_bookmarks"] = []
                st.error(f"Unable to read bookmarks: {e}")

        bookmarks = st.session_state.get("split_bookmarks", [])
        stored_pdf_bytes = st.session_state.get("split_pdf_bytes")

        if bookmarks and stored_pdf_bytes:
            options = [f"L{bm['level']} | P{bm['page'] + 1} | {bm['title']}" for bm in bookmarks]
            selected_label = st.selectbox("Select bookmark from where Part 2 should start", options)
            selected_idx = options.index(selected_label)

            bm = bookmarks[selected_idx]
            st.info(
                f"Split point selected: **{bm['title']}** (starts at page **{bm['page'] + 1}**).\n\n"
                f"Part 1 = pages 1 to {bm['page']} | Part 2 = page {bm['page'] + 1} onward"
            )

            if st.button("Create Part 1 + Part 2", type="primary"):
                try:
                    part1_output, part2_output, selected_bm, split_page, total_pages = split_into_two_parts(
                        stored_pdf_bytes, bookmarks, selected_idx
                    )
                    st.success("Split completed successfully. Bookmarks preserved in both outputs where available.")

                    dl1, dl2 = st.columns(2)
                    dl1.download_button(
                        label="Download Part 1",
                        data=part1_output,
                        file_name=f"{safe_filename(output_base_name)}_Part1.pdf",
                        mime="application/pdf"
                    )
                    dl2.download_button(
                        label="Download Part 2",
                        data=part2_output,
                        file_name=f"{safe_filename(output_base_name)}_Part2.pdf",
                        mime="application/pdf"
                    )

                    st.caption(
                        f"Selected bookmark: {selected_bm['title']} | "
                        f"Part 1 pages: 1 to {split_page} | "
                        f"Part 2 pages: {split_page + 1} to {total_pages}"
                    )
                except Exception as e:
                    st.error(f"Error while splitting PDF: {e}")
        elif split_file is not None:
            st.caption("Click 'Load Bookmarks' to read available bookmarks from the uploaded PDF.")

st.divider()
st.caption("Note: Streamlit web upload/runtime limits still apply for very large PDFs. Use the desktop EXE for large MRB files.")
