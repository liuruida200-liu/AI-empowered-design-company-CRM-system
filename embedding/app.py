import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)
warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

import uuid
import torch
import streamlit as st
from dotenv import load_dotenv
from PyPDF2 import PdfReader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings, HuggingFacePipeline
from langchain_community.vectorstores import FAISS
from transformers import AutoModelForCausalLM, AutoTokenizer, pipeline
from langchain_classic.memory import ConversationBufferMemory
from langchain_classic.chains import ConversationalRetrievalChain
from langchain_core.prompts import PromptTemplate
from db import init_db, store_document_pages, store_chunks, store_chat_message


def get_pdf_text(pdf_docs):
    text = ""
    for pdf in pdf_docs:
        pdf_reader = PdfReader(pdf)
        filename = pdf.name if hasattr(pdf, "name") else str(pdf)
        pages = []
        for page in pdf_reader.pages:
            page_text = page.extract_text() or ""
            pages.append(page_text)
            text += page_text
        store_document_pages(filename, pages)
    return text


def get_text_chunks(text):
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=100,
        length_function=len,
    )
    chunks = text_splitter.split_text(text)
    store_chunks(chunks)
    return chunks


def get_vectorstore(text_chunks):
    embeddings = HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-MiniLM-L6-v2")
    vectorstore = FAISS.from_texts(texts=text_chunks, embedding=embeddings)
    return vectorstore


def get_conversation_chain(vectorstore):
    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Using device: {device}")

    model_id = "Qwen/Qwen2.5-1.5B-Instruct"
    tokenizer = AutoTokenizer.from_pretrained(model_id)
    model = AutoModelForCausalLM.from_pretrained(
        model_id, dtype=torch.float16 if device == "cuda" else torch.float32
    ).to(device)
    tokenizer.pad_token = tokenizer.eos_token
    model.generation_config.max_new_tokens = 512
    model.generation_config.max_length = None
    pipe = pipeline("text-generation", model=model, tokenizer=tokenizer,
                    device=model.device, return_full_text=False)
    llm = HuggingFacePipeline(pipeline=pipe)

    custom_prompt = PromptTemplate(
        input_variables=["context", "question"],
        template=(
            "Based on the following context, answer the question concisely.\n\n"
            "Context:\n{context}\n\n"
            "Question: {question}\n\n"
            "Answer:"
        ),
    )

    memory = ConversationBufferMemory(
        memory_key='chat_history', return_messages=True)
    conversation_chain = ConversationalRetrievalChain.from_llm(
        llm=llm,
        retriever=vectorstore.as_retriever(
            search_type="similarity", search_kwargs={"k": 4}),
        memory=memory,
        combine_docs_chain_kwargs={"prompt": custom_prompt},
    )
    return conversation_chain


def main():
    load_dotenv()
    init_db()
    st.set_page_config(page_title="Chat with PDFs", page_icon="\U0001F916")

    if "conversation" not in st.session_state:
        st.session_state.conversation = None
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())

    st.header("Chat with PDFs \U0001F916")

    with st.sidebar:
        st.subheader("Your documents")
        pdf_docs = st.file_uploader(
            "Upload your PDFs here and click on 'Process'", accept_multiple_files=True)
        if st.button("Process"):
            with st.spinner("Processing"):
                raw_text = get_pdf_text(pdf_docs)
                text_chunks = get_text_chunks(raw_text)
                vectorstore = get_vectorstore(text_chunks)
                st.session_state.conversation = get_conversation_chain(vectorstore)
                st.success("PDFs processed successfully!")

    # Display chat history
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # Chat input at bottom
    if user_question := st.chat_input("Ask questions about your documents"):
        if st.session_state.conversation is None:
            st.warning("Please upload and process PDFs first.")
        else:
            st.session_state.messages.append({"role": "user", "content": user_question})
            store_chat_message(st.session_state.session_id, "user", user_question)
            with st.chat_message("user"):
                st.markdown(user_question)

            with st.chat_message("assistant"):
                with st.spinner("Thinking..."):
                    response = st.session_state.conversation({"question": user_question})
                    answer = response["answer"]
                    st.markdown(answer)
            st.session_state.messages.append({"role": "assistant", "content": answer})
            store_chat_message(st.session_state.session_id, "assistant", answer)


if __name__ == '__main__':
    main()