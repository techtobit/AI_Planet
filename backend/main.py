from fastapi import FastAPI, File, UploadFile, HTTPException, Depends, Query
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.ext.declarative import declarative_base
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import sessionmaker
from datetime import datetime
import fitz  
import os
import uuid
from langchain_community.vectorstores import Chroma
from langchain_community.vectorstores import FAISS
from langchain_community.llms import OpenAI
from langchain_openai import OpenAIEmbeddings 
from langchain.text_splitter import CharacterTextSplitter
from langchain.chains.question_answering import load_qa_chain
import logging
from sentence_transformers import SentenceTransformer
import faiss 
import numpy as np
from transformers import pipeline



app = FastAPI()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


origins = ["http://localhost", "http://localhost:8080", "http://localhost:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Database setup
DATABASE_URL = "sqlite:///./test.db"
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String, index=True)
    upload_date = Column(DateTime, default=datetime.utcnow)
    text_content = Column(Text)

Base.metadata.create_all(bind=engine)

# Ensure the uploads directory exists
if not os.path.exists('uploads'):
    os.makedirs('uploads')


# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# Upload PDF Endpoint
@app.post("/upload/")
async def upload_pdf(file: UploadFile = File(...), db: SessionLocal = Depends(get_db)):
    if not file.filename.endswith('.pdf'):
        raise HTTPException(status_code=400, detail="File must be a PDF")

    file_path = f"uploads/{uuid.uuid4()}.pdf"
    with open(file_path, "wb") as buffer:
        buffer.write(file.file.read())

    # Extract text from PDF
    doc = fitz.open(file_path)
    text_content = ""
    for page in doc:
        text_content += page.get_text()

    # Save document metadata and text content to database
    db_document = Document(filename=file.filename, text_content=text_content)
    db.add(db_document)
    db.commit()
    db.refresh(db_document)

    return {"id": db_document.id, "filename": db_document.filename}


@app.post("/ask/")
async def ask_question(document_id: int = Query(...), question: str = Query(...), db: SessionLocal = Depends(get_db)):
    try:
        document = db.query(Document).filter(Document.id == document_id).first()
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")

        text_splitter = CharacterTextSplitter(chunk_size=1000, chunk_overlap=600)
        texts = text_splitter.split_text(document.text_content)

        model = SentenceTransformer('all-MiniLM-L6-v2')  
        embeddings = model.encode(texts, show_progress_bar=True)
        print('embeddings printing - ', embeddings)

        
        dimension = embeddings.shape[1]
        index = faiss.IndexFlatL2(dimension) 
        index.add(embeddings)

        # Encode the question to find similar documents
        question_embedding = model.encode([question])
        distances, indices = index.search(question_embedding, k=20) 

        # Retrieve the most relevant texts
        relevant_texts = [texts[i] for i in indices[0]]

        # Use Hugging Face's pipeline for QA
        qa_pipeline = pipeline("question-answering")
        context = " ".join(relevant_texts)
        result = qa_pipeline(question=question, context=context)
        answer = result['answer']
        print('result :-', answer)
        return {"question": question, "answer": answer}
    except HTTPException as e:
        logger.error(f"HTTP error: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"Error processing question: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


@app.get("/")
async def main():
    return {"message": "Hello World"}
