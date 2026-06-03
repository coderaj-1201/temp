"""
create_search_index.py
======================
Creates the single unified AI Search index (idx-rag) with:
  - Filterable `domain` field (hr / legal / it)
  - content_vector field for dense retrieval (1536 dims for ada-002)
  - Semantic configuration for reranking

Run once before deploying:
  cd container-code
  python scripts/create_search_index.py

Auth: az login (Contributor + Search Index Data Contributor role needed)
"""
from __future__ import annotations

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from azure.identity import AzureCliCredential
from azure.search.documents.indexes import SearchIndexClient
from azure.search.documents.indexes.models import (
    HnswAlgorithmConfiguration,
    SearchField,
    SearchFieldDataType,
    SearchIndex,
    SearchableField,
    SemanticConfiguration,
    SemanticField,
    SemanticPrioritizedFields,
    SemanticSearch,
    SimpleField,
    VectorSearch,
    VectorSearchProfile,
)

from shared.config import settings


def create_index():
    credential = AzureCliCredential()
    client = SearchIndexClient(
        endpoint=str(settings.AZURE_SEARCH_ENDPOINT),
        credential=credential,
    )

    fields = [
        SimpleField(name="id",     type=SearchFieldDataType.String, key=True, filterable=True),
        SearchableField(name="content", type=SearchFieldDataType.String, analyzer_name="en.microsoft"),
        SimpleField(name="source", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SimpleField(name="domain", type=SearchFieldDataType.String, filterable=True, facetable=True),
        SimpleField(name="url",    type=SearchFieldDataType.String),
        SearchField(
            name="content_vector",
            type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
            searchable=True,
            vector_search_dimensions=1536,
            vector_search_profile_name="hnsw-profile",
        ),
    ]

    vector_search = VectorSearch(
        algorithms=[HnswAlgorithmConfiguration(name="hnsw", parameters={"m": 4, "efConstruction": 400})],
        profiles=[VectorSearchProfile(name="hnsw-profile", algorithm_configuration_name="hnsw")],
    )

    semantic_search = SemanticSearch(
        configurations=[
            SemanticConfiguration(
                name=settings.AZURE_SEARCH_SEMANTIC_CONFIG,
                prioritized_fields=SemanticPrioritizedFields(
                    content_fields=[SemanticField(field_name="content")],
                    keywords_fields=[SemanticField(field_name="source"), SemanticField(field_name="domain")],
                ),
            )
        ]
    )

    index = SearchIndex(
        name=settings.AZURE_SEARCH_INDEX,
        fields=fields,
        vector_search=vector_search,
        semantic_search=semantic_search,
    )

    result = client.create_or_update_index(index)
    print(f"✅ Index '{result.name}' created/updated at {settings.AZURE_SEARCH_ENDPOINT}")
    print(f"   Fields: {[f.name for f in result.fields]}")
    print(f"   Semantic config: {settings.AZURE_SEARCH_SEMANTIC_CONFIG}")


if __name__ == "__main__":
    create_index()
