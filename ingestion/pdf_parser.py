from ingestion.models import QuestionDTO

def parse_pdf(pdf_path: str) -> list[QuestionDTO]:
    """
    Your existing extract_pages → parse_mcqs → harvest_references
    pipeline from before. Returns list[QuestionDTO].
    Already implemented — just wrap the return value in QuestionDTO.
    """
    ...  