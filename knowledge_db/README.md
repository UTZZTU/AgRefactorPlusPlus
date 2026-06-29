# Long-term memory stores (knowledge_db)

AgRefactor's self-evolving memory is a [ChromaDB](https://www.trychroma.com/)
vector store of successful and failed refactoring trials (see the paper,
"Long-term Memory for HLS Refactoring"). Embeddings use
`all-MiniLM-L6-v2` (SentenceTransformers).

**The pre-accumulated memory stores used in the paper are not committed here**
(they are large and are an experimental artifact). You have two options:

1. **Build your own** by running the accumulation flow over the training
   benchmarks (3 epochs in the paper):

   ```bash
   python -m flow.parallel_kernel \
       --exp_name mem_accumulation \
       --kernels_file flow/train_kernels.json \
       --enable_rag --enable_rag_update \
       --repeat 1 --max_workers 20
   ```

   This populates a persistent store (default path `./knowledge_db/tmp_db`,
   configurable via `--knowledge_db_path`).

2. **Request the paper's pre-built stores** from the authors.

At inference time, point the flow at a store with `--knowledge_db_path` and
enable retrieval with `--enable_rag`.
