from toram_skills.hybrid_search import FusionConfig

DEFAULT_EMBEDDING_PROVIDER = 'sentence-transformers'
DEFAULT_EMBEDDING_MODEL = 'sentence-transformers/all-MiniLM-L6-v2'
DEFAULT_EMBEDDING_CONFIG_ID = 'symmetric-encode-v1'
DEFAULT_FUSION_CONFIG = FusionConfig(
    rrf_k=20,
    lexical_weight=1.5,
    semantic_weight=1.0,
)
