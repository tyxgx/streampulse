# Roles, tech stacks and gaps (from the 2026-09-24 session `85b9556e`, re-checked 2026-09-25)

Goal he stated: make CV + portfolio stronger for Deep Learning, ML Engineer, NLP, LLM/GenAI, MLOps and AI Engineer roles via one new heavy project plus stack add-ons to liveflights, StreamPulse, TeamBoard, interactive-ml.

## Stack per role (as given to him)
| Role | Stack |
|---|---|
| Deep Learning Eng | Python, PyTorch, TensorFlow/Keras, CUDA basics, mixed precision (AMP), NumPy/Pandas/sklearn, ONNX/TorchScript, W&B/TensorBoard, Git/Docker/Linux |
| ML Engineer | PyTorch, sklearn, XGBoost/LightGBM, FastAPI/Flask/gRPC, Docker, Kubernetes, MLflow, DVC, SQL, Spark, SageMaker/Vertex, GitHub Actions |
| NLP Engineer | HuggingFace (Transformers/Datasets/Tokenizers), BERT/RoBERTa, spaCy/NLTK, Sentence-Transformers, Elasticsearch/OpenSearch, NER/classification |
| LLM / GenAI Eng | LangChain/LlamaIndex/LangGraph, vector DBs (FAISS, Chroma, Pinecone, pgvector, Qdrant), LoRA/QLoRA/PEFT/TRL, vLLM/Ollama/llama.cpp, LLM APIs, RAGAS/DeepEval, guardrails, Neo4j GraphRAG |
| MLOps Eng | Kubernetes, Kubeflow, Airflow/Prefect/Dagster, MLflow, Feast, Terraform, Prometheus/Grafana, Evidently/Great Expectations, Argo, Jenkins/GH Actions, SageMaker/EKS |
| AI Engineer | LLM APIs, RAG (vector DB + embeddings + reranker), FastAPI, Next.js/React, LangChain/LangGraph, MCP, evals/tracing (LangSmith, Langfuse), Docker, Vercel/Render/AWS |
(Also discussed: Computer Vision, Speech, Research Scientist, ML Infra, Edge AI - unrealistic/low priority for a fresher.)

## What he already has (real, in code)
RAG + pgvector (StreamPulse), Neo4j GraphRAG + tool-calling agent (hyperquest), MLflow, Airflow (4 DAGs, CI-verified), dbt, Spark/Kafka/Delta, Terraform, Docker, GitHub Actions, FastAPI, Next.js/React, Groq/Gemini APIs (3-tier fallback), DuckDB pipeline (StreamPulse daily refresh).

## Real gaps (audit 2026-09-22, `~/job/skills-gap-tracker.md`)
1. **PyTorch/Keras**: claimed, zero use in any repo. Biggest risk.
2. Great Expectations / Evidently (liveflights `quality_checks.py` says "plain SQL").
3. Statistics / A-B testing. 4. Kubernetes. 5. Snowflake/Databricks (talking point only). 6. Tableau (claimed, no workbook verified).
Also: 0 ONNX, no LangGraph/MCP/Langfuse/RAGAS/reranker.
Suggested learning order: PyTorch -> HuggingFace -> embeddings/reranker -> Great Expectations/Evidently -> Kubernetes basics. CUDA/TensorRT/vLLM/Feast only if a role asks.

## StreamPulse add-ons planned (only what is genuinely missing)
Cross-encoder reranker + hybrid (BM25 + vector) search; RAGAS on the existing `baseline_*.json`; Langfuse tracing; SQL-router vs RAG as a LangGraph agent; guardrails + semantic cache. Ownership rule: only the `automation`-branch ETL and the StreamPulse layer are his (CDAC capstone, see global memory `cdac_group_project_integrity`).

## Other projects (plans exist in the 09-24 chat, not built)
- liveflights: 6-phase plan (README fix; Great Expectations + Evidently; PyTorch GRU/LSTM vs GBM vs dead-reckoning; ONNX serving + live trajectory; shadow A/B with natural ground truth; kind/k8s; MCP). The GRU work itself is running in the liveflights session (globe_history data).
- interactive-ml: offline PyTorch -> ONNX Runtime Web, Optuna, SHAP, fix algorithm-count mismatch (3/5/7).
- TeamBoard: pgvector semantic search, duplicate detection, LLM standup summary (small).
- hyperquest: resume Neo4j Aura (backend 404), vector index + hybrid GraphRAG, RAGAS, MCP.
- JobLens (job-market fit-score engine) was the recommended "new heavy project" on 09-24; NOT chosen. He then pivoted to data-first for StreamPulse (this folder).

## Rules that apply to every CV claim
Only claim a stack after code exists and metrics are measured. Time window: hiring is strong until ~10 Oct (Diwali slowdown), he must be home by 25 Oct -> keep scope small. 8 GB Mac: training on Colab/Kaggle GPU or GitHub Actions, not locally.
