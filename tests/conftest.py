"""Shared fixtures for tests."""

from __future__ import annotations

from pathlib import Path


class MockConfig:
    litellm_api_base = None
    litellm_api_key = None
    tree_generation_model = "openai/test"
    reasoning_model = "openai/test"
    notes_model = "openai/test"
    embedding_model = "openai/test"
    embedding_dimension = 1024
    vault_root = Path(".")
    log_dir = None
    max_liner_notes_per_citation = 4
    reasoning_api_base = None
    reasoning_api_key = None
    num_retries = 1
    completion_timeout = 30
    rate_limit_retries = 2
    rate_limit_max_wait = 5
    reasoning_mode = "agentic"
    teleport_max = 3
    shortlist_size = 3


# Node 001's text carries <physical_index_N> markers (as produced by
# if_add_node_text_labels="yes"); node 002 is unlabeled to exercise the fallback.
SAMPLE_TREE_OUTPUT = {
    "doc_name": "sample_report",
    "doc_description": "A sample financial report for testing.",
    "structure": [
        {
            "title": "Executive Summary",
            "node_id": "001",
            "start_index": 1,
            "end_index": 3,
            "summary": "Overview of fiscal year results.",
            "text": (
                "<physical_index_1>\nThe company achieved record revenue of $10B this year.\n<physical_index_1>\n"
                "<physical_index_2>\nOperating margins expanded to 31%.\n<physical_index_2>\n"
                "<physical_index_3>\nHeadcount grew modestly.\n<physical_index_3>\n"
            ),
            "nodes": [
                {
                    "title": "Revenue Highlights",
                    "node_id": "001.001",
                    "start_index": 1,
                    "end_index": 2,
                    "summary": "Cloud revenue grew 42% YoY.",
                    "text": (
                        "<physical_index_1>\nCloud services revenue reached $4.2B, up 42% year-over-year.\n<physical_index_1>\n"
                        "<physical_index_2>\nSubscription renewals stayed above 95%.\n<physical_index_2>\n"
                    ),
                }
            ],
        },
        {
            "title": "Risk Factors",
            "node_id": "002",
            "start_index": 4,
            "end_index": 8,
            "summary": "Key risks facing the business.",
            "text": "Market competition and regulatory changes pose risks.",
            "nodes": [],
        },
    ],
}
