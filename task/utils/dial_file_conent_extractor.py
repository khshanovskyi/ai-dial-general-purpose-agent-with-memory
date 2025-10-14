import io
from pathlib import Path

import PyPDF2
import pandas as pd
from aidial_client import Dial
from bs4 import BeautifulSoup


class DialFileContentExtractor:

    def __init__(self, endpoint: str, api_key: str):
        self.dial_client = Dial(
            base_url=endpoint,
            api_key=api_key,
        )

    def extract_text(self, file_url: str) -> str:
        file_download_response = self.dial_client.files.download(file_url)
        filename = file_download_response.filename
        file_content: bytes = file_download_response.get_content()

        file_extension = Path(filename).suffix.lower()
        text_content = self.__extract_text(file_content, file_extension, filename)

        return text_content

    def __extract_text(self, file_content: bytes, file_extension: str, filename: str) -> str:
        """Extract text content based on file type."""
        try:
            if file_extension == '.txt':
                return file_content.decode('utf-8', errors='ignore')

            elif file_extension == '.pdf':
                pdf_file = io.BytesIO(file_content)
                pdf_reader = PyPDF2.PdfReader(pdf_file)
                text = []
                for page in pdf_reader.pages:
                    text.append(page.extract_text())
                return '\n'.join(text)

            elif file_extension == '.csv':
                text_content = file_content.decode('utf-8', errors='ignore')
                csv_buffer = io.StringIO(text_content)
                df = pd.read_csv(csv_buffer)
                return df.to_markdown(index=False)

            elif file_extension in ['.html', '.htm']:
                html_content = file_content.decode('utf-8', errors='ignore')
                soup = BeautifulSoup(html_content, 'html.parser')

                # Remove script and style elements
                for script in soup(["script", "style"]):
                    script.decompose()

                return soup.get_text(separator='\n', strip=True)

            else:
                # Fallback: try to decode as text
                return file_content.decode('utf-8', errors='ignore')

        except Exception as e:
            print(f"Error extracting text from {filename}: {str(e)}")
            return ""
