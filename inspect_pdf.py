import pdfplumber
import sys

def inspect_pdf(file_path):
    with pdfplumber.open(file_path) as pdf:
        print("--- Page 1 Text ---")
        page = pdf.pages[0]
        text = page.extract_text()
        print(text)

        print("\n--- Page 1 Tables ---")
        tables = page.extract_tables()
        if tables:
            for i, table in enumerate(tables):
                print(f"\n--- Table {i+1} ---")
                for row in table:
                    print(row)
        else:
            print("No tables found on page 1.")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        inspect_pdf(sys.argv[1])
    else:
        print("Please provide the path to the PDF file.")
