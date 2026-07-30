import litellm
import logging
import os
import re
from datetime import datetime
import email.utils
import time
import random
import json
import pypdf
import asyncio
import pymupdf
from dataclasses import dataclass
from io import BytesIO
import yaml
from pathlib import Path
from types import SimpleNamespace as config

litellm.drop_params = True
# Don't print the "Give Feedback / Get Help" banner on every mapped exception;
# our retry loop already logs each 429/transient with its own context.
litellm.suppress_debug_info = True

_log = logging.getLogger("librarian.pageindex")

# Transient exceptions worth retrying at the gateway level. RateLimitError is
# listed for completeness but is handled by its own, more patient policy.
_TRANSIENT = (
    litellm.RateLimitError,
    litellm.ServiceUnavailableError,
    litellm.APIConnectionError,
    litellm.InternalServerError,
    litellm.Timeout,
    TimeoutError,
)


@dataclass(frozen=True)
class RetryPolicy:
    """Retry behaviour for the litellm gateway wrappers.

    429s get a separate, more patient budget than other transient errors:
    budget hosts with per-minute subscription limits need minutes-scale waits,
    honoring the Retry-After header when the provider sends one.
    """

    num_retries: int = 3            # retries for non-429 transient errors
    backoff_max: float = 30.0       # per-wait cap for non-429 transients (seconds)
    rate_limit_retries: int = 10    # retries for 429s
    rate_limit_max_wait: float = 120.0  # per-wait cap for 429s; also caps Retry-After
    timeout: int = 300              # per-call litellm timeout (seconds)


class LLMCallError(Exception):
    """LLM call failed after exhausting retries."""


class LLMRateLimitExhausted(LLMCallError):
    """Provider kept returning 429 past the retry budget."""

# ── Explicit LLM configuration (no env-var mutation) ─────────────────────────
# The upstream fork read OPENAI_API_BASE/OPENAI_API_KEY from the environment;
# here the consumer calls configure_llm() once at process edge and the values
# are passed to litellm per call.

_api_base: str | None = None
_api_key: str | None = None
_log_dir: str | None = None
_retry_policy: RetryPolicy = RetryPolicy()


def configure_llm(api_base: str | None = None, api_key: str | None = None,
                  max_concurrent: int | None = None, log_dir: str | None = None,
                  retry_policy: RetryPolicy | None = None) -> None:
    """Configure the LLM gateway. Call once before ingestion/query.

    log_dir enables JsonLogger diagnostic output; left unset, logging is a no-op
    (never writes relative to the current working directory).
    """
    global _api_base, _api_key, _log_dir, _retry_policy
    if api_base is not None:
        _api_base = api_base
    if api_key is not None:
        _api_key = api_key
    if log_dir is not None:
        _log_dir = log_dir
    if max_concurrent is not None:
        set_max_concurrent_llm_calls(max_concurrent)
    if retry_policy is not None:
        _retry_policy = retry_policy


# ── Concurrency limiter for async LLM fan-out ────────────────────────────────

_llm_semaphore: asyncio.Semaphore | None = None
_max_concurrent: int = 10


def set_max_concurrent_llm_calls(n: int) -> None:
    """Set the max number of concurrent async LLM calls. Call before ingestion."""
    global _llm_semaphore, _max_concurrent
    _max_concurrent = n
    _llm_semaphore = None  # reset so next access creates a fresh semaphore


def _get_semaphore() -> asyncio.Semaphore:
    global _llm_semaphore
    if _llm_semaphore is None:
        _llm_semaphore = asyncio.Semaphore(_max_concurrent)
    return _llm_semaphore

def count_tokens(text, model=None):
    if not text:
        return 0
    return litellm.token_counter(model=model, text=text)


# ── Retry policy machinery ────────────────────────────────────────────────────

# Shared cooldown: when any call gets a 429, every gateway call waits until the
# deadline before its next attempt, so concurrent fan-out backs off together
# instead of each task rediscovering the same rate limit.
_cooldown_until: float = 0.0


def _extend_cooldown(delay: float) -> None:
    global _cooldown_until
    _cooldown_until = max(_cooldown_until, time.monotonic() + delay)


def _cooldown_remaining() -> float:
    return max(0.0, _cooldown_until - time.monotonic())


def _retry_after_seconds(exc) -> float | None:
    """Retry-After from a litellm exception: proxy headers dict first, then the
    (possibly synthesized) httpx response. Numeric seconds or HTTP-date."""
    value = None
    exc_headers = getattr(exc, "headers", None)
    if exc_headers:
        value = {k.lower(): v for k, v in exc_headers.items()}.get("retry-after")
    if value is None:
        response = getattr(exc, "response", None)
        if response is not None and getattr(response, "headers", None) is not None:
            value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            dt = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if dt is None:
            return None
        seconds = (dt - datetime.now(dt.tzinfo)).total_seconds()
    return seconds if seconds > 0 else None


def _rate_limit_delay(exc, attempt: int, policy: RetryPolicy) -> tuple[float, str]:
    retry_after = _retry_after_seconds(exc)
    if retry_after is not None:
        return min(retry_after + random.uniform(0, 1), policy.rate_limit_max_wait), "retry-after"
    return min(4 * 2 ** attempt + random.uniform(0, 2), policy.rate_limit_max_wait), "backoff"


def _transient_delay(attempt: int, policy: RetryPolicy) -> float:
    return min(2 ** attempt + random.uniform(0, 1), policy.backoff_max)


def _handle_retryable(e, rl_attempts: int, transient_attempts: int,
                      model, policy: RetryPolicy) -> tuple[int, int, float]:
    """Book-keeping shared by the sync/async wrappers for one failed attempt.

    Returns updated (rl_attempts, transient_attempts, sleep_seconds); raises the
    typed exception once the relevant budget is exhausted. 429 waits go through
    the shared cooldown (sleep_seconds 0 — the caller waits on the cooldown);
    other transients sleep locally.
    """
    if isinstance(e, litellm.RateLimitError):
        rl_attempts += 1
        if rl_attempts > policy.rate_limit_retries:
            raise LLMRateLimitExhausted(
                f"rate limited after {policy.rate_limit_retries} retries (model={model}): {e}"
            ) from e
        delay, source = _rate_limit_delay(e, rl_attempts, policy)
        _extend_cooldown(delay)
        _log.warning("Rate limited (attempt %d/%d): waiting %.1fs (source=%s)",
                     rl_attempts, policy.rate_limit_retries, delay, source)
        return rl_attempts, transient_attempts, 0.0
    transient_attempts += 1
    if transient_attempts > policy.num_retries:
        raise LLMCallError(
            f"transient errors exhausted after {policy.num_retries} retries (model={model}): {e}"
        ) from e
    _log.warning("Transient error (attempt %d/%d): %s",
                 transient_attempts, policy.num_retries, e)
    return rl_attempts, transient_attempts, _transient_delay(transient_attempts, policy)


def llm_completion(model, prompt, chat_history=None, return_finish_reason=False):
    if model:
        model = model.removeprefix("litellm/")
    policy = _retry_policy
    messages = list(chat_history) + [{"role": "user", "content": prompt}] if chat_history else [{"role": "user", "content": prompt}]
    rl_attempts = 0
    transient_attempts = 0
    while True:
        remaining = _cooldown_remaining()
        if remaining > 0:
            # Extra jitter spreads wake-ups so concurrent tasks don't retry in
            # one burst the moment the shared cooldown expires; re-check after
            # sleeping in case another task extended the deadline meanwhile.
            time.sleep(remaining + random.uniform(0, 2))
            continue
        try:
            response = litellm.completion(
                model=model,
                messages=messages,
                temperature=0,
                timeout=policy.timeout,
                api_base=_api_base,
                api_key=_api_key,
            )
            content = response.choices[0].message.content
            if return_finish_reason:
                finish_reason = "max_output_reached" if response.choices[0].finish_reason == "length" else "finished"
                return content, finish_reason
            return content
        except _TRANSIENT as e:
            rl_attempts, transient_attempts, sleep_s = _handle_retryable(
                e, rl_attempts, transient_attempts, model, policy)
            if sleep_s > 0:
                time.sleep(sleep_s)


async def llm_acompletion(model, prompt):
    if model:
        model = model.removeprefix("litellm/")
    policy = _retry_policy
    messages = [{"role": "user", "content": prompt}]
    rl_attempts = 0
    transient_attempts = 0
    while True:
        # Wait out any shared 429 cooldown before taking a semaphore slot; the
        # jitter spreads wake-ups so tasks don't retry in one burst, and the
        # re-check catches deadlines extended while we slept.
        remaining = _cooldown_remaining()
        if remaining > 0:
            await asyncio.sleep(remaining + random.uniform(0, 2))
            continue
        try:
            async with _get_semaphore():
                if _cooldown_remaining() > 0:
                    # A 429 landed while we queued for this slot — back off
                    # instead of burning a request into the limited window.
                    continue
                response = await litellm.acompletion(
                    model=model,
                    messages=messages,
                    temperature=0,
                    timeout=policy.timeout,
                    api_base=_api_base,
                    api_key=_api_key,
                )
            return response.choices[0].message.content
        except _TRANSIENT as e:
            rl_attempts, transient_attempts, sleep_s = _handle_retryable(
                e, rl_attempts, transient_attempts, model, policy)
            if sleep_s > 0:
                await asyncio.sleep(sleep_s)


def get_json_content(response):
    start_idx = response.find("```json")
    if start_idx != -1:
        start_idx += 7
        response = response[start_idx:]

    end_idx = response.rfind("```")
    if end_idx != -1:
        response = response[:end_idx]

    json_content = response.strip()
    return json_content


def extract_json(content):
    try:
        # Strip thinking-mode blocks (e.g. Qwen3 <think>...</think>)
        content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()

        # First, try to extract JSON enclosed within ```json and ```
        start_idx = content.find("```json")
        if start_idx != -1:
            start_idx += 7  # Adjust index to start after the delimiter
            end_idx = content.rfind("```")
            json_content = content[start_idx:end_idx].strip()
        else:
            # If no delimiters, assume entire content could be JSON
            json_content = content.strip()

        # Clean up common issues that might cause parsing errors
        json_content = json_content.replace('None', 'null')  # Replace Python None with JSON null
        json_content = json_content.replace('\n', ' ').replace('\r', ' ')  # Remove newlines
        json_content = ' '.join(json_content.split())  # Normalize whitespace

        # Use raw_decode to parse only the first valid JSON value, ignoring
        # any trailing text the model appended after the JSON object
        obj, _ = json.JSONDecoder().raw_decode(json_content)
        return obj
    except json.JSONDecodeError as e:
        logging.error(f"Failed to extract JSON: {e}")
        # Try to clean up the content further if initial parsing fails
        try:
            # Remove any trailing commas before closing brackets/braces
            json_content = json_content.replace(',]', ']').replace(',}', '}')
            obj, _ = json.JSONDecoder().raw_decode(json_content)
            return obj
        except:
            logging.error("Failed to parse JSON even after cleanup")
            return {}
    except Exception as e:
        logging.error(f"Unexpected error while extracting JSON: {e}")
        return {}

def write_node_id(data, node_id=0):
    if isinstance(data, dict):
        data['node_id'] = str(node_id).zfill(4)
        node_id += 1
        for key in list(data.keys()):
            if 'nodes' in key:
                node_id = write_node_id(data[key], node_id)
    elif isinstance(data, list):
        for index in range(len(data)):
            node_id = write_node_id(data[index], node_id)
    return node_id

def structure_to_list(structure):
    if isinstance(structure, dict):
        nodes = []
        nodes.append(structure)
        if 'nodes' in structure:
            nodes.extend(structure_to_list(structure['nodes']))
        return nodes
    elif isinstance(structure, list):
        nodes = []
        for item in structure:
            nodes.extend(structure_to_list(item))
        return nodes


def sanitize_filename(filename, replacement='-'):
    # In Linux, only '/' and '\0' (null) are invalid in filenames.
    # Null can't be represented in strings, so we only handle '/'.
    return filename.replace('/', replacement)

def get_pdf_name(pdf_path):
    # Extract PDF name
    if isinstance(pdf_path, str):
        pdf_name = os.path.basename(pdf_path)
    elif isinstance(pdf_path, BytesIO):
        pdf_reader = pypdf.PdfReader(pdf_path)
        meta = pdf_reader.metadata
        pdf_name = meta.title if meta and meta.title else 'Untitled'
        pdf_name = sanitize_filename(pdf_name)
    return pdf_name


class JsonLogger:
    """Diagnostic JSON log, one file per document run.

    Writes only when a log directory has been set via configure_llm(log_dir=...);
    otherwise every call is a no-op so library use never touches the filesystem.
    """

    def __init__(self, file_path):
        pdf_name = get_pdf_name(file_path)
        current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.filename = f"{pdf_name}_{current_time}.json"
        self.log_data = []
        if _log_dir:
            os.makedirs(_log_dir, exist_ok=True)

    def log(self, level, message, **kwargs):
        if not _log_dir:
            return
        if isinstance(message, dict):
            self.log_data.append(message)
        else:
            self.log_data.append({'message': message})
        with open(self._filepath(), "w") as f:
            json.dump(self.log_data, f, indent=2)

    def info(self, message, **kwargs):
        self.log("INFO", message, **kwargs)

    def error(self, message, **kwargs):
        self.log("ERROR", message, **kwargs)

    def debug(self, message, **kwargs):
        self.log("DEBUG", message, **kwargs)

    def exception(self, message, **kwargs):
        kwargs["exception"] = True
        self.log("ERROR", message, **kwargs)

    def _filepath(self):
        return os.path.join(_log_dir, self.filename)


def list_to_tree(data):
    def get_parent_structure(structure):
        """Helper function to get the parent structure code"""
        if not structure:
            return None
        parts = str(structure).split('.')
        return '.'.join(parts[:-1]) if len(parts) > 1 else None

    # First pass: Create nodes and track parent-child relationships
    nodes = {}
    root_nodes = []

    for item in data:
        structure = item.get('structure')
        node = {
            'title': item.get('title'),
            'start_index': item.get('start_index'),
            'end_index': item.get('end_index'),
            'nodes': []
        }

        nodes[structure] = node

        # Find parent
        parent_structure = get_parent_structure(structure)

        if parent_structure:
            # Add as child to parent if parent exists
            if parent_structure in nodes:
                nodes[parent_structure]['nodes'].append(node)
            else:
                root_nodes.append(node)
        else:
            # No parent, this is a root node
            root_nodes.append(node)

    # Helper function to clean empty children arrays
    def clean_node(node):
        if not node['nodes']:
            del node['nodes']
        else:
            for child in node['nodes']:
                clean_node(child)
        return node

    # Clean and return the tree
    return [clean_node(node) for node in root_nodes]

def add_preface_if_needed(data):
    if not isinstance(data, list) or not data:
        return data

    if data[0]['physical_index'] is not None and data[0]['physical_index'] > 1:
        preface_node = {
            "structure": "0",
            "title": "Preface",
            "physical_index": 1,
        }
        data.insert(0, preface_node)
    return data



def get_page_tokens(pdf_path, model=None, pdf_parser="pypdf"):
    # "PyPDF2" still accepted: upstream's name for what is now pypdf.
    if pdf_parser in ("pypdf", "PyPDF2"):
        pdf_reader = pypdf.PdfReader(pdf_path)
        page_list = []
        for page_num in range(len(pdf_reader.pages)):
            page = pdf_reader.pages[page_num]
            page_text = page.extract_text()
            token_length = litellm.token_counter(model=model, text=page_text)
            page_list.append((page_text, token_length))
        return page_list
    elif pdf_parser == "PyMuPDF":
        if isinstance(pdf_path, BytesIO):
            pdf_stream = pdf_path
            doc = pymupdf.open(stream=pdf_stream, filetype="pdf")
        elif isinstance(pdf_path, str) and os.path.isfile(pdf_path) and pdf_path.lower().endswith(".pdf"):
            doc = pymupdf.open(pdf_path)
        page_list = []
        for page in doc:
            page_text = page.get_text()
            token_length = litellm.token_counter(model=model, text=page_text)
            page_list.append((page_text, token_length))
        return page_list
    else:
        raise ValueError(f"Unsupported PDF parser: {pdf_parser}")



def get_text_of_pdf_pages(pdf_pages, start_page, end_page):
    text = ""
    for page_num in range(start_page-1, end_page):
        text += pdf_pages[page_num][0]
    return text

def get_text_of_pdf_pages_with_labels(pdf_pages, start_page, end_page):
    text = ""
    for page_num in range(start_page-1, end_page):
        text += f"<physical_index_{page_num+1}>\n{pdf_pages[page_num][0]}\n<physical_index_{page_num+1}>\n"
    return text


def post_processing(structure, end_physical_index):
    # First convert page_number to start_index in flat list
    for i, item in enumerate(structure):
        item['start_index'] = item.get('physical_index')
        if i < len(structure) - 1:
            if structure[i + 1].get('appear_start') == 'yes':
                item['end_index'] = structure[i + 1]['physical_index']-1
            else:
                item['end_index'] = structure[i + 1]['physical_index']
        else:
            item['end_index'] = end_physical_index
    tree = list_to_tree(structure)
    if len(tree)!=0:
        return tree
    else:
        ### remove appear_start
        for node in structure:
            node.pop('appear_start', None)
            node.pop('physical_index', None)
        return structure


def remove_structure_text(data):
    if isinstance(data, dict):
        data.pop('text', None)
        if 'nodes' in data:
            remove_structure_text(data['nodes'])
    elif isinstance(data, list):
        for item in data:
            remove_structure_text(item)
    return data


def convert_physical_index_to_int(data):
    if isinstance(data, list):
        for i in range(len(data)):
            # Check if item is a dictionary and has 'physical_index' key
            if isinstance(data[i], dict) and 'physical_index' in data[i]:
                if isinstance(data[i]['physical_index'], str):
                    if data[i]['physical_index'].startswith('<physical_index_'):
                        data[i]['physical_index'] = int(data[i]['physical_index'].split('_')[-1].rstrip('>').strip())
                    elif data[i]['physical_index'].startswith('physical_index_'):
                        data[i]['physical_index'] = int(data[i]['physical_index'].split('_')[-1].strip())
    elif isinstance(data, str):
        if data.startswith('<physical_index_'):
            data = int(data.split('_')[-1].rstrip('>').strip())
        elif data.startswith('physical_index_'):
            data = int(data.split('_')[-1].strip())
        # Check data is int
        if isinstance(data, int):
            return data
        else:
            return None
    return data


def convert_page_to_int(data):
    for item in data:
        if 'page' in item and isinstance(item['page'], str):
            try:
                item['page'] = int(item['page'])
            except ValueError:
                # Keep original value if conversion fails
                pass
    return data


def add_node_text(node, pdf_pages):
    if isinstance(node, dict):
        start_page = node.get('start_index')
        end_page = node.get('end_index')
        node['text'] = get_text_of_pdf_pages(pdf_pages, start_page, end_page)
        if 'nodes' in node:
            add_node_text(node['nodes'], pdf_pages)
    elif isinstance(node, list):
        for index in range(len(node)):
            add_node_text(node[index], pdf_pages)
    return


def add_node_text_with_labels(node, pdf_pages):
    if isinstance(node, dict):
        start_page = node.get('start_index')
        end_page = node.get('end_index')
        node['text'] = get_text_of_pdf_pages_with_labels(pdf_pages, start_page, end_page)
        if 'nodes' in node:
            add_node_text_with_labels(node['nodes'], pdf_pages)
    elif isinstance(node, list):
        for index in range(len(node)):
            add_node_text_with_labels(node[index], pdf_pages)
    return


async def generate_node_summary(node, model=None):
    prompt = f"""You are given a part of a document, your task is to generate a description of the partial document about what are main points covered in the partial document.

    Partial Document Text: {node['text']}

    Directly return the description, do not include any other text.
    """
    response = await llm_acompletion(model, prompt)
    return response


async def generate_summaries_for_structure(structure, model=None):
    nodes = structure_to_list(structure)
    tasks = [generate_node_summary(node, model=model) for node in nodes]
    summaries = await asyncio.gather(*tasks)

    for node, summary in zip(nodes, summaries):
        node['summary'] = summary
    return structure


def create_clean_structure_for_description(structure):
    """
    Create a clean structure for document description generation,
    excluding unnecessary fields like 'text'.
    """
    if isinstance(structure, dict):
        clean_node = {}
        # Only include essential fields for description
        for key in ['title', 'node_id', 'summary', 'prefix_summary']:
            if key in structure:
                clean_node[key] = structure[key]

        # Recursively process child nodes
        if 'nodes' in structure and structure['nodes']:
            clean_node['nodes'] = create_clean_structure_for_description(structure['nodes'])

        return clean_node
    elif isinstance(structure, list):
        return [create_clean_structure_for_description(item) for item in structure]
    else:
        return structure


def generate_doc_description(structure, model=None):
    prompt = f"""Your are an expert in generating descriptions for a document.
    You are given a structure of a document. Your task is to generate a one-sentence description for the document, which makes it easy to distinguish the document from other documents.

    Document Structure: {structure}

    Directly return the description, do not include any other text.
    """
    response = llm_completion(model, prompt)
    return response


def reorder_dict(data, key_order):
    if not key_order:
        return data
    return {key: data[key] for key in key_order if key in data}


def format_structure(structure, order=None):
    if not order:
        return structure
    if isinstance(structure, dict):
        if 'nodes' in structure:
            structure['nodes'] = format_structure(structure['nodes'], order)
        if not structure.get('nodes'):
            structure.pop('nodes', None)
        structure = reorder_dict(structure, order)
    elif isinstance(structure, list):
        structure = [format_structure(item, order) for item in structure]
    return structure


class ConfigLoader:
    def __init__(self, default_path: str = None):
        if default_path is None:
            default_path = Path(__file__).parent / "config.yaml"
        self._default_dict = self._load_yaml(default_path)

    @staticmethod
    def _load_yaml(path):
        with open(path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _validate_keys(self, user_dict):
        unknown_keys = set(user_dict) - set(self._default_dict)
        if unknown_keys:
            raise ValueError(f"Unknown config keys: {unknown_keys}")

    def load(self, user_opt=None) -> config:
        """
        Load the configuration, merging user options with default values.
        """
        if user_opt is None:
            user_dict = {}
        elif isinstance(user_opt, config):
            user_dict = vars(user_opt)
        elif isinstance(user_opt, dict):
            user_dict = user_opt
        else:
            raise TypeError("user_opt must be dict, config(SimpleNamespace) or None")

        self._validate_keys(user_dict)
        merged = {**self._default_dict, **user_dict}
        return config(**merged)
