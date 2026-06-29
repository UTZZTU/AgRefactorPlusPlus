import os, json, chromadb, time, random  # type: ignore
import numpy as np
from typing import Dict, List, Tuple, Optional, Any
from dataclasses import dataclass
from datetime import datetime
from chromadb.config import Settings  # type: ignore
from sentence_transformers import SentenceTransformer  # type: ignore

@dataclass
class SuccessfulTrial:
    trial_id: str
    timestamp: str
    original_code: str
    identified_items: List[str]
    plan: Dict[str, Any]
    plan_hetero: Optional[Dict[str, Any]] = None
    plan_type: str = "main"
    code_embedding: Optional[List[float]] = None
    items_embedding: Optional[List[float]] = None


@dataclass
class FailedTrial:
    trial_id: str
    timestamp: str
    original_code: str
    identified_items: List[str]
    missing_items: List[str]
    synthesis_error: str
    code_embedding: Optional[List[float]] = None


class KnowledgeDB:
    def __init__(
        self,
        db_path: str = "./tmp/hls_knowledge_db",
        embedding_model: str = "all-MiniLM-L6-v2",
        reset_db: bool = False,
        debug: int = 0
    ):
        
        self.db_path = db_path
        self.debug = debug
        
        self.encoder = SentenceTransformer(embedding_model, device='cuda')
        
        os.makedirs(db_path, exist_ok=True)
        
        # ChromaDB initialization with retry logic for multiprocessing
        max_retries = 5
        retry_delay = 0.2  # Start with 200ms
        
        for attempt in range(max_retries):
            try:
                settings = Settings(
                    anonymized_telemetry=False,
                    allow_reset=True,
                    is_persistent=True,
                    persist_directory=db_path
                )
                self.client = chromadb.Client(settings)
                
                # Collection creation with retry
                self.successful_trials_collection = self.client.get_or_create_collection("successful_trials")
                self.failed_trials_collection = self.client.get_or_create_collection("failed_trials")
                
                # Success - break out of retry loop
                if self.debug >= 1 and attempt > 0:
                    print(f"ChromaDB initialized successfully on attempt {attempt + 1}")
                break
                
            except Exception as e:
                if attempt < max_retries - 1:  # Not the last attempt
                    if self.debug >= 1:
                        print(f"ChromaDB init attempt {attempt + 1} failed: {e}. Retrying...")
                    
                    # Exponential backoff with jitter to avoid thundering herd
                    sleep_time = retry_delay + random.uniform(0, 0.1)
                    time.sleep(sleep_time)
                else:
                    # Final attempt failed
                    raise Exception(f"Failed to initialize ChromaDB after {max_retries} attempts: {e}")
        
        self.successful_trials: Dict[str, SuccessfulTrial] = {}
        self.failed_trials: Dict[str, FailedTrial] = {}
        
        if reset_db:
            self._reset_database()
    
    def _reset_database(self):
        if self.debug >= 1:
            print("Resetting Knowledge Database...")
        
        self.client.delete_collection("successful_trials")
        self.client.delete_collection("failed_trials")
        self.successful_trials_collection = self.client.create_collection("successful_trials")
        self.failed_trials_collection = self.client.create_collection("failed_trials")
        
        self.successful_trials = {}
        self.failed_trials = {}
    
    def _generate_code_embedding(self, code: str) -> List[float]:
        code_clean = code.strip()
        embedding = self.encoder.encode(code_clean)
        return embedding.tolist()
    
    def _generate_items_embedding(self, items: List[str]) -> List[float]:
        if not items:
            return [0.0] * self.encoder.get_sentence_embedding_dimension()
        
        items_text = ". ".join(items)
        embedding = self.encoder.encode(items_text)
        return embedding.tolist()
    
    def add_successful_trial(
        self,
        original_code: str,
        identified_items: List[str],
        plan: Dict[str, Any],
        trial_id: Optional[str] = None,
        plan_hetero: Optional[Dict[str, Any]] = None,
        plan_type: str = "main"
    ) -> str:
        if trial_id is None:
            trial_id = f"success_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        
        code_embedding = self._generate_code_embedding(original_code)
        items_embedding = self._generate_items_embedding(identified_items)
        
        trial = SuccessfulTrial(
            trial_id=trial_id,
            timestamp=datetime.now().isoformat(),
            original_code=original_code,
            identified_items=identified_items,
            plan=plan,
            plan_hetero=plan_hetero,
            plan_type=plan_type,
            code_embedding=code_embedding,
            items_embedding=items_embedding
        )
        
        self.successful_trials_collection.add(
            ids=[trial_id],
            documents=[original_code],
            embeddings=[code_embedding],
            metadatas=[{
                "timestamp": trial.timestamp,
                "num_identified_items": len(identified_items),
                "items_text": ". ".join(identified_items) if identified_items else "",
                "identified_items_list": json.dumps(identified_items),
                "plan": json.dumps(plan),
                "plan_hetero": json.dumps(plan_hetero or {}),
                "plan_type": plan_type,
            }]
        )
        self.successful_trials[trial_id] = trial
        if self.debug >= 1:
            print(f"Added successful trial: {trial_id}")
        
        return trial_id
    
    def add_failed_trial(
        self,
        original_code: str,
        identified_items: List[str],
        missing_items: List[str],
        synthesis_error: str,
        trial_id: Optional[str] = None
    ) -> str:
        if trial_id is None:
            trial_id = f"failed_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
        
        code_embedding = self._generate_code_embedding(original_code)
        
        trial = FailedTrial(
            trial_id=trial_id,
            timestamp=datetime.now().isoformat(),
            original_code=original_code,
            identified_items=identified_items,
            missing_items=missing_items,
            synthesis_error=synthesis_error,
            code_embedding=code_embedding
        )
        
        self.failed_trials_collection.add(
            ids=[trial_id],
            documents=[original_code],
            embeddings=[code_embedding],
            metadatas=[{
                "timestamp": trial.timestamp,
                "num_identified_items": len(identified_items),
                "num_missing_items": len(missing_items),
                "missing_items_text": ". ".join(missing_items) if missing_items else "",
                "error_snippet": synthesis_error,
                "identified_items_list": json.dumps(identified_items),
                "missing_items_list": json.dumps(missing_items),
            }]
        )
        
        self.failed_trials[trial_id] = trial
        
        if self.debug >= 1:
            print(f"Added failed trial: {trial_id}")
        
        return trial_id
    
    def retrieve_similar_failures(
        self,
        code: str,
        n_results: int = 5,
        threshold: float = 1.5
    ) -> List[Tuple[FailedTrial, float]]:
        if self.failed_trials_collection.count() == 0:
            return []
        code_embedding = self._generate_code_embedding(code)
        
        results = self.failed_trials_collection.query(
            query_embeddings=[code_embedding],
            n_results=n_results
        )
        
        similar_trials: List[Tuple[FailedTrial, float]] = []
        ids = results.get("ids", [[]])[0]
        distances = results.get("distances", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        documents = results.get("documents", [[]])[0]
        for trial_id, distance, meta, doc in zip(ids, distances, metadatas, documents):
            if distance <= threshold:
                trial = FailedTrial(
                    trial_id=trial_id,
                    timestamp=meta.get("timestamp", ""),
                    original_code=doc or "",
                    identified_items=json.loads(meta.get("identified_items_list", "[]")),
                    missing_items=json.loads(meta.get("missing_items_list", "[]")),
                    synthesis_error=meta.get("error_snippet", ""),
                    code_embedding=code_embedding
                )
                similar_trials.append((trial, distance))
        
        if self.debug >= 2:
            print(f"Found {len(similar_trials)} similar failed trials within threshold {threshold}")
        
        return similar_trials
    
    def retrieve_relevant_plans(
        self,
        code: str,
        identified_items: List[str],
        n_results: int = 3,
        code_weight: float = 0.6,
        items_weight: float = 0.4,
        threshold: float = 1.5,
        plan_type: str = "main"
    ) -> List[Tuple[SuccessfulTrial, float]]:
        if self.successful_trials_collection.count() == 0:
            return []
        code_embedding = np.array(self._generate_code_embedding(code))
        items_embedding = np.array(self._generate_items_embedding(identified_items))
        
        # Retry logic for ChromaDB get() to handle multiprocess race conditions
        max_retries = 5
        retry_delay = 0.2
        store = None
        
        for attempt in range(max_retries):
            try:
                store = self.successful_trials_collection.get(
                    include=["embeddings", "metadatas", "documents"]
                )
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    if self.debug >= 1:
                        print(f"ChromaDB get() attempt {attempt + 1} failed: {e}. Retrying...")
                    time.sleep(retry_delay + random.uniform(0, 0.1))
                else:
                    if self.debug >= 1:
                        print(f"ChromaDB get() failed after {max_retries} attempts: {e}. Returning empty result.")
                    return []
        
        if store is None:
            return []
            
        ids: List[str] = store.get("ids", [])
        embeddings: List[List[float]] = store.get("embeddings", [])
        metadatas: List[Dict[str, Any]] = store.get("metadatas", [])
        documents: List[str] = store.get("documents", [])
        relevant_plans: List[Tuple[SuccessfulTrial, float]] = []

        for trial_id, emb, meta, doc in zip(ids, embeddings, metadatas, documents):
            if meta.get("plan_type", "main") != plan_type:
                continue
            trial_code_embedding = np.array(emb)
            code_distance = np.linalg.norm(code_embedding - trial_code_embedding)
            trial_ident_items = json.loads(meta.get("identified_items_list", "[]"))
            trial_items_embedding = np.array(self._generate_items_embedding(trial_ident_items))
            items_distance = np.linalg.norm(items_embedding - trial_items_embedding)
            weighted_distance = code_weight * code_distance + items_weight * items_distance
            if weighted_distance <= threshold:
                trial = SuccessfulTrial(
                    trial_id=trial_id,
                    timestamp=meta.get("timestamp", ""),
                    original_code=doc or "",
                    identified_items=trial_ident_items,
                    plan=json.loads(meta.get("plan", "{}")),
                    plan_hetero=json.loads(meta.get("plan_hetero", "{}")),
                    plan_type=meta.get("plan_type", "main"),
                    code_embedding=emb,
                    items_embedding=trial_items_embedding.tolist()
                )
                relevant_plans.append((trial, weighted_distance))

        relevant_plans.sort(key=lambda x: x[1])
        relevant_plans = relevant_plans[:n_results]
        
        if self.debug >= 2:
            print(f"Found {len(relevant_plans)} relevant plans within threshold {threshold}")
        
        return relevant_plans
    
    def get_common_missing_items(
        self,
        code: str,
        n_results: int = 5,
        threshold: float = 1.5
    ) -> List[str]:
        similar_failures = self.retrieve_similar_failures(code, n_results, threshold)
        
        if not similar_failures:
            return []
        
        missing_items_counts = {}
        total_trials = len(similar_failures)
        
        for trial, _ in similar_failures:
            for item in trial.missing_items:
                if item not in missing_items_counts:
                    missing_items_counts[item] = 0
                missing_items_counts[item] += 1
        
        threshold_count = max(1, int(0.3 * total_trials))
        common_missing_items = [
            item for item, count in missing_items_counts.items()
            if count >= threshold_count
        ]
        
        if self.debug >= 2:
            print(f"Found {len(common_missing_items)} common missing items from {total_trials} similar trials")
        
        return common_missing_items
    
    def get_stats(self) -> Dict[str, Any]:
        try:
            successful_count = self.successful_trials_collection.count()
        except Exception:
            successful_count = len(self.successful_trials)
        try:
            failed_count = self.failed_trials_collection.count()
        except Exception:
            failed_count = len(self.failed_trials)
        return {
            "successful_trials": successful_count,
            "failed_trials": failed_count,
            "total_trials": successful_count + failed_count,
            "db_path": self.db_path
        }
