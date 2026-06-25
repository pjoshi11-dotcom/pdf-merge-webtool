import sys
from io import BytesIO
from typing import List, Dict, Any

import streamlit as st
from pypdf import PdfReader, PdfWriter

sys.setrecursionlimit(5000)
st.set_page_config(page_title="PDF Merge & Split Tool", layout="wide")


def safe_filename(name: str) -> str:
    invalid = '<>:"/\\|?*'
    cleaned = ''.join('_' if c in invalid else c for c in name).strip()
    return cleaned[:120] or 'Output'


def parse_outline_nodes(outline, reader, level=1, parent_id=None, result=None):
    """Parse pypdf outline into ordered nodes while preserving parent-child relationships."""
    if result is None:
        result = []
    last_node_id = None
    for item in outline:
        if isinstance(item, list):
            child_parent = last_node_id if last_node_id is not None else parent_id
            parse_outline_nodes(item, reader, level + 1, child_parent, result)
        else:
            try:
                title = getattr(item, 'title', None) or item.get('/Title')
            except Exception:
                title = str(item)
            try:
                page_num = reader.get_destination_page_number(item)
            except Exception:
                page_num = None
            if title is not None and page_num is not None and page_num >= 0:
                ancestors = []
                anc = parent_id
                while anc is not None:
                    ancestors.append(anc)
                    anc = result[anc]['parent_id']
                node_id = len(result)
                result.append({
                    'id': node_id,
                    'title': str(title),
                    'page': int(page_num),
                    'level': int(level),
                    'parent_id': parent_id,
                    'ancestors': ancestors,  # nearest parent first
                })
                last_node_id = node_id
    return result


def subset_bookmarks(nodes: List[Dict[str, Any]], start_page: int, end_page: int):
    subset = [dict(n) for n in nodes if start_page <= n['page'] < end_page]
    for n in subset:
        n['adj_page'] = n['page'] - start_page
    return subset


def add_bookmarks_to_writer(writer: PdfWriter, subset_nodes: List[Dict[str, Any]]):
    if not subset_nodes:
        return
    added_refs = {}
    for n in subset_nodes:
        parent_ref = None
        for anc in n['ancestors']:
            if anc in added_refs:
                parent_ref = added_refs[anc]
                break
        ref = writer.add_outline_item(title=n['title'], page_number=n['adj_page'], parent=parent_ref)
        added_refs[n['id']] = ref


def merge_files_with_bookmarks(sorted_files):
    writer = PdfWriter()
    try:
        for _, up_file in sorted_files:
            up_file.seek(0)
            writer.append(up_file, import_outline=True)
        output = BytesIO()
        writer.write(output)
        return output.getvalue()
    finally:
        try:
            writer.close()
        except Exception:
            pass


def create_parts_from_split_points(pdf_bytes: bytes, nodes: List[Dict[str, Any]], selected_node_ids: List[int]):
    reader = PdfReader(BytesIO(pdf_bytes))
    total_pages = len(reader.pages)

    selected_pages = sorted(set(nodes[nid]['page'] for nid in selected_node_ids if 0 <= nodes[nid]['page'] < total_pages))
    cut_points = [p for p in selected_pages if 0 < p < total_pages]
    if not cut_points:
        raise ValueError("Selected split points do not create any valid cut in the document.")

    boundaries = [0] + cut_points + [total_pages]
    segments = [(boundaries[i], boundaries[i + 1]) for i in range(len(boundaries) - 1) if boundaries[i] < boundaries[i + 1]]
    outputs = []

    for idx, (start_page, end_page) in enumerate(segments, start=1):
        writer = PdfWriter()
        for page_no in range(start_page, end_page):
            writer.add_page(reader.pages[page_no])
        subset = subset_bookmarks(nodes, start_page, end_page)
        add_bookmarks_to_writer(writer, subset)
        output = BytesIO()
        writer.write(output)
        outputs.append({
            'part_no': idx,
            'start_page': start_page + 1,
            'end_page': end_page,
            'bytes': output.getvalue(),
        })
        try:
            writer.close()
        except Exception:
            pass

    return outputs, cut_points, total_pages


def init_state():
    defaults = {
        'split_nodes': [],
        'split_pdf_bytes': None,
        'split_file_token': None,
        'split_selected_ids': [],
        'split_outputs': None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def reset_split_state(file_token=None, pdf_bytes=None):
    st.session_state['split_nodes'] = []
    st.session_state['split_selected_ids'] = []
    st.session_state['split_outputs'] = None
    st.session_state['split_file_token'] = file_token
    st.session_state['split_pdf_bytes'] = pdf_bytes


def node_label(n: Dict[str, Any]) -> str:
    indent = '  ' * max(0, n['level'] - 1)
    return f"{indent}P{n['page'] + 1} | L{n['level']} | {n['title']}"


def top_level_nodes(nodes):
    return [n for n in nodes if n['parent_id'] is None]


def children_of(nodes, parent_id):
    return [n for n in nodes if n['parent_id'] == parent_id]


def node_matches_filter(node, query: str) -> bool:
    return query in node['title'].lower() if query else True


def subtree_has_match(nodes_map, node_id, query: str) -> bool:
    node = nodes_map[node_id]
    if node_matches_filter(node, query):
        return True
    for child_id in [n['id'] for n in nodes_map.values() if n['parent_id'] == node_id]:
        if subtree_has_match(nodes_map, child_id, query):
            return True
    return False


def render_tree_section(nodes: List[Dict[str, Any]], query: str):
    # Near-desktop replica: top-level expanders + nested indented items with add buttons.
    if not nodes:
        st.info("No bookmarks loaded yet.")
        return

    nodes_map = {n['id']: n for n in nodes}
    selected_ids = set(st.session_state['split_selected_ids'])
    expand_all = st.checkbox("Expand all top-level sections", value=False, key="expand_all_sections")

    for root in top_level_nodes(nodes):
        if not subtree_has_match(nodes_map, root['id'], query):
            continue
        with st.expander(f"P{root['page'] + 1} | {root['title']}", expanded=expand_all):
            render_node_recursive(nodes_map, root['id'], query, selected_ids)


def render_node_recursive(nodes_map, node_id, query: str, selected_ids: set):
    node = nodes_map[node_id]
    if node['parent_id'] is not None and subtree_has_match(nodes_map, node_id, query):
        c1, c2 = st.columns([8, 2])
        c1.write(f"{'— ' * max(0, node['level'] - 2)}P{node['page'] + 1} | {node['title']}")
        if node_id in selected_ids:
            c2.caption("Added")
        else:
            if c2.button("Add", key=f"add_node_{node_id}"):
                st.session_state['split_selected_ids'] = sorted(set(st.session_state['split_selected_ids'] + [node_id]), key=lambda nid: nodes_map[nid]['page'])
                st.rerun()

    children = [n for n in nodes_map.values() if n['parent_id'] == node_id and subtree_has_match(nodes_map, n['id'], query)]
    for child in sorted(children, key=lambda x: (x['page'], x['id'])):
        render_node_recursive(nodes_map, child['id'], query, selected_ids)


init_state()

st.title("PDF Merge & Split Tool")
st.caption("Web replica of the desktop tool: merge with bookmarks preserved, search bookmarks, browse hierarchy, and split into multiple parts using one or more split points.")

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
        hdr = st.columns([6, 2])
        hdr[0].markdown("**File**")
        hdr[1].markdown("**Sequence**")
        for i, file in enumerate(uploaded_files, start=1):
            row = st.columns([6, 2])
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
                merged_bytes = merge_files_with_bookmarks(sorted_files)
                st.success("PDF merged successfully with bookmarks preserved where available.")
                st.download_button(
                    label="Download Merged PDF",
                    data=merged_bytes,
                    file_name=f"{safe_filename(output_merge_name)}.pdf",
                    mime="application/pdf",
                    key="download_merged_pdf"
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
        current_token = f"{split_file.name}_{split_file.size}"
        if st.session_state['split_file_token'] != current_token:
            reset_split_state(file_token=current_token, pdf_bytes=split_file.getvalue())

        top_btns = st.columns([1, 5])
        load_clicked = top_btns[0].button("Load Bookmarks", key="load_bookmarks_btn")
        top_btns[1].caption("Add one or more split points. Each selected bookmark becomes a cut point. Example: choose Section C and Section E → Part 1, Part 2, Part 3.")

        if load_clicked:
            try:
                pdf_bytes = st.session_state['split_pdf_bytes']
                reader = PdfReader(BytesIO(pdf_bytes))
                outline = getattr(reader, 'outline', None)
                if outline is None:
                    st.session_state['split_nodes'] = []
                    st.warning("No bookmarks/outlines found in this PDF.")
                else:
                    st.session_state['split_nodes'] = parse_outline_nodes(outline, reader)
                    st.session_state['split_selected_ids'] = []
                    st.session_state['split_outputs'] = None
                    st.success(f"Loaded {len(st.session_state['split_nodes'])} bookmark(s).")
            except Exception as e:
                st.session_state['split_nodes'] = []
                st.session_state['split_selected_ids'] = []
                st.session_state['split_outputs'] = None
                st.error(f"Unable to read bookmarks: {e}")

        nodes = st.session_state['split_nodes']
        pdf_bytes = st.session_state['split_pdf_bytes']

        if nodes and pdf_bytes:
            st.markdown("### Search bookmarks")
            search_cols = st.columns([3, 2, 1])
            search_text = search_cols[0].text_input("Search text", value="", key="split_search_text")
            matches = [n for n in nodes if search_text.lower() in n['title'].lower()] if search_text.strip() else []
            search_options = [node_label(n) for n in matches]
            selected_match = search_cols[1].selectbox("Matches", options=search_options if search_options else ["No matches"], key="split_search_match")
            if search_cols[2].button("Add Match", key="add_match_btn") and matches:
                match_node = matches[search_options.index(selected_match)]
                st.session_state['split_selected_ids'] = sorted(set(st.session_state['split_selected_ids'] + [match_node['id']]), key=lambda nid: next(x['page'] for x in nodes if x['id'] == nid))
                st.rerun()

            st.markdown("### Bookmark hierarchy")
            render_tree_section(nodes, search_text.strip().lower())

            st.markdown("### Selected split points")
            selected_nodes = [n for n in nodes if n['id'] in st.session_state['split_selected_ids']]
            selected_nodes = sorted(selected_nodes, key=lambda x: (x['page'], x['id']))
            if selected_nodes:
                for n in selected_nodes:
                    c1, c2 = st.columns([8, 1])
                    c1.write(f"P{n['page'] + 1} | L{n['level']} | {n['title']}")
                    if c2.button("Remove", key=f"remove_selected_{n['id']}"):
                        st.session_state['split_selected_ids'] = [x for x in st.session_state['split_selected_ids'] if x != n['id']]
                        st.session_state['split_outputs'] = None
                        st.rerun()
            else:
                st.info("No split points added yet.")

            action_cols = st.columns([1, 1, 6])
            if action_cols[0].button("Clear Split Points", key="clear_split_points_btn"):
                st.session_state['split_selected_ids'] = []
                st.session_state['split_outputs'] = None
                st.rerun()
            if action_cols[1].button("Create All Parts", type="primary", key="create_all_parts_btn"):
                try:
                    outputs, cut_points, total_pages = create_parts_from_split_points(pdf_bytes, nodes, st.session_state['split_selected_ids'])
                    st.session_state['split_outputs'] = {
                        'outputs': outputs,
                        'cut_points': cut_points,
                        'total_pages': total_pages,
                        'base_name': safe_filename(output_base_name),
                    }
                except Exception as e:
                    st.session_state['split_outputs'] = None
                    st.error(f"Error while splitting PDF: {e}")

            results = st.session_state.get('split_outputs')
            if results:
                st.success(f"Split completed successfully. Created {len(results['outputs'])} parts. Relevant bookmarks preserved in each output where available.")
                info_text = " | ".join([f"Part {o['part_no']}: pages {o['start_page']} to {o['end_page']}" for o in results['outputs']])
                st.caption(info_text)

                for out in results['outputs']:
                    st.download_button(
                        label=f"Download Part {out['part_no']}",
                        data=out['bytes'],
                        file_name=f"{results['base_name']}_Part{out['part_no']}.pdf",
                        mime="application/pdf",
                        key=f"download_part_{out['part_no']}",
                        on_click="ignore"
                    )
        elif split_file is not None:
            st.caption("Click 'Load Bookmarks' to read available bookmarks from the uploaded PDF.")

st.divider()
st.caption("Note: This web version closely replicates the desktop features, but Streamlit upload/runtime limits still apply for very large MRB PDFs. For large files, the desktop EXE remains the best option.")
