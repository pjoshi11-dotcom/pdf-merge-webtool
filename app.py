import streamlit as st
from pypdf import PdfWriter
from io import BytesIO
import sys

# Helpful for larger PDFs / deep bookmark trees
sys.setrecursionlimit(max(5000, sys.getrecursionlimit() * 5))

st.set_page_config(page_title="PDF Merge Tool", layout="centered")
st.title("PDF Merge Tool")

uploaded_files = st.file_uploader(
    "Upload PDF files",
    type=["pdf"],
    accept_multiple_files=True
)

if uploaded_files:
    st.subheader("Set Merge Sequence")

    file_orders = []
    for i, file in enumerate(uploaded_files, start=1):
        seq = st.number_input(
            f"Sequence for {file.name}",
            min_value=1,
            max_value=len(uploaded_files),
            value=i,
            step=1,
            key=file.name
        )
        file_orders.append((seq, file))

    if st.button("Merge PDFs"):
        sorted_files = sorted(file_orders, key=lambda x: x[0])

        writer = PdfWriter()

        try:
            for _, pdf_file in sorted_files:
                pdf_file.seek(0)
                # This preserves bookmarks/outlines where available
                writer.append(pdf_file, import_outline=True)

            output = BytesIO()
            writer.write(output)
            writer.close()
            output.seek(0)

            st.success("PDF merged successfully with bookmarks preserved where available!")

            st.download_button(
                label="Download Merged PDF",
                data=output,
                file_name="Merged_Output.pdf",
                mime="application/pdf"
            )

        except Exception as e:
            st.error(f"Error while merging PDFs: {e}")
