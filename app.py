import streamlit as st
from pypdf import PdfReader, PdfWriter
from io import BytesIO

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

        for _, pdf_file in sorted_files:
            reader = PdfReader(pdf_file)
            for page in reader.pages:
                writer.add_page(page)

        output = BytesIO()
        writer.write(output)
        output.seek(0)

        st.success("PDF merged successfully!")

        st.download_button(
            label="Download Merged PDF",
            data=output,
            file_name="Merged_Output.pdf",
            mime="application/pdf"
        )